from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

from deskremote.core.config import default_config
from deskremote.raumfeld.media import (
    ALBUMS, ARTISTS, SEARCH_ALBUMS, MediaContainer, Page, container_uri, parse_didl_page,
)
from deskremote.raumfeld.upnp import UpnpError
from deskremote.ui.music import MusicWindow

FIX = Path(__file__).parent / "fixtures"
ALBUMS_XML = (FIX / "media_albums.xml").read_text(encoding="utf-8")
TRACKS_XML = (FIX / "media_album_tracks.xml").read_text(encoding="utf-8")
FIRST_ALBUM = "0/My Music/Albums/Naked Lunch+Album%2001"
UDN = "uuid:c24a80f6-f43a-51a1-9031-97cedeb3c9f3"


class FakeMS:
    def __init__(self, udn=UDN, name="Raumfeld MediaServer", total=20, fail=False, raumfeld=True):
        self.udn, self.name, self.total, self.fail = udn, name, total, fail
        self.is_raumfeld = raumfeld
        self.host = "192.168.0.176"
        self.calls = []

    def browse(self, object_id, start=0, count=100):
        self.calls.append(("browse", object_id, start))
        if self.fail:
            raise UpnpError("weg")
        if object_id == FIRST_ALBUM:
            return parse_didl_page(TRACKS_XML, 9, start)
        if object_id == ARTISTS:
            return Page([MediaContainer("0/My Music/Artists/X", "Naked Lunch", None, 2, None, "artist")], 1, 0)
        if object_id == "0/My Music/Artists/X":
            return Page([MediaContainer("0/x/all", "All Tracks by Naked Lunch", None, 9, None, "folder"),
                         MediaContainer(FIRST_ALBUM, "Album 01", "Naked Lunch", 9, None, "album")], 2, 0)
        page = parse_didl_page(ALBUMS_XML, self.total, start)
        return page

    def metadata(self, object_id):
        self.calls.append(("metadata", object_id))
        return f"<DIDL-Lite id='{object_id}'/>"

    def search(self, container_id, text, start=0, count=100):
        self.calls.append(("search", container_id, text))
        return Page([MediaContainer("0/s/1", "Dummy Love", "Sophie Hunger", 10, None, "album")], 1, 0)


def window(qtbot, servers=None, cfg=None):
    cfg = cfg or default_config()
    cfg.target_room = cfg.target_room or "uuid:bad"  # Abspielen braucht ein Ziel
    saved = []
    w = MusicWindow(servers if servers is not None else [FakeMS()], None, cfg, save=saved.append, run_async=False)
    qtbot.addWidget(w)
    if w._servers:
        w.show_albums()  # die meisten Tests arbeiten mit der Albenliste
    return w, cfg, saved


def btn(w, name):
    return w.findChild(QPushButton, name)


def lbl(w, name):
    return w.findChild(QLabel, name)


def test_album_view_lists_albums(qtbot):
    w, _, _ = window(qtbot)
    assert lbl(w, "title:0").text() == "Album 01 · Naked Lunch"
    assert lbl(w, "heading").text() == "Alben"


def test_play_album_emits_container_uri_from_start(qtbot):
    w, _, _ = window(qtbot)
    with qtbot.waitSignal(w.play) as blocker:
        qtbot.mouseClick(btn(w, "play:0"), Qt.LeftButton)
    assert blocker.args == [container_uri(UDN, FIRST_ALBUM, 0), f"<DIDL-Lite id='{FIRST_ALBUM}'/>"]


def test_play_track_three_emits_fii_2(qtbot):
    w, _, _ = window(qtbot)
    qtbot.mouseClick(btn(w, "open:0"), Qt.LeftButton)
    with qtbot.waitSignal(w.play) as blocker:
        qtbot.mouseClick(btn(w, "playtrack:2"), Qt.LeftButton)
    assert blocker.args[0] == container_uri(UDN, FIRST_ALBUM, 2)


def test_open_album_shows_tracks_and_back_returns(qtbot):
    w, _, _ = window(qtbot)
    qtbot.mouseClick(btn(w, "open:0"), Qt.LeftButton)
    assert lbl(w, "track:0").text().startswith("1  Titel 03")
    assert lbl(w, "heading").text() == "Album 01 · Naked Lunch"
    qtbot.mouseClick(btn(w, "back"), Qt.LeftButton)
    assert lbl(w, "heading").text() == "Alben" and lbl(w, "title:0") is not None


def test_music_loads_next_page_on_scroll(qtbot):
    ms = FakeMS(total=250)
    w, _, _ = window(qtbot, [ms])
    w.scroll_to_end()
    assert ("browse", ALBUMS, 20) in ms.calls  # weiter ab den tatsächlich erhaltenen Einträgen


def test_music_source_error_shows_message(qtbot):
    w, _, _ = window(qtbot, [FakeMS(fail=True)])
    assert lbl(w, "status").text() == "Quelle nicht erreichbar"


def test_music_labels_are_plain_text(qtbot):
    ms = FakeMS()
    ms.browse = lambda *a, **k: Page([MediaContainer("0/a", "<img src='file://x/y'>", "<b>A</b>", 1, None, "album")], 1, 0)
    w, _, _ = window(qtbot, [ms])
    labels = w.findChildren(QLabel)
    assert labels and all(l.textFormat() == Qt.PlainText for l in labels)


def test_last_source_remembered(qtbot):
    a, b = FakeMS(udn="uuid:a", name="Raumfeld MediaServer"), FakeMS(udn="uuid:b", name="DS923", raumfeld=False)
    cfg = default_config()
    cfg.music_source = "uuid:b"
    w, cfg, saved = window(qtbot, [a, b], cfg)
    combo = w.findChild(QComboBox, "source")
    assert combo.currentData() == "uuid:b"
    combo.setCurrentIndex(0)
    assert cfg.music_source == "uuid:a" and saved


def test_artist_view_shows_only_albums(qtbot):
    w, _, _ = window(qtbot)
    w.show_artists()
    qtbot.mouseClick(btn(w, "open:0"), Qt.LeftButton)
    assert lbl(w, "title:0").text() == "Album 01 · Naked Lunch"
    assert lbl(w, "title:1") is None


def test_search_uses_raumfeld_album_search(qtbot):
    ms = FakeMS()
    w, _, _ = window(qtbot, [ms])
    w.search_now("love")
    assert ("search", SEARCH_ALBUMS, "love") in ms.calls
    assert lbl(w, "title:0").text() == "Dummy Love · Sophie Hunger"


def test_no_server_message(qtbot):
    w, _, _ = window(qtbot, [])
    assert "Keine Musikquelle" in lbl(w, "status").text()


def test_discover_sources_in_background(qtbot):
    w, _, _ = window(qtbot, [])
    w.discover(lambda: [FakeMS()])
    qtbot.waitUntil(lambda: lbl(w, "title:0") is not None, timeout=2000)
    assert w.findChild(QComboBox, "source").itemText(0) == "Meine Musik (Raumfeld)"


def test_discover_failure_shows_message(qtbot):
    w, _, _ = window(qtbot, [])

    def boom():
        raise OSError("kein Netz")

    w.discover(boom)
    qtbot.waitUntil(lambda: lbl(w, "status").text() == "Quelle nicht erreichbar", timeout=2000)


def test_foreign_source_cannot_pose_as_raumfeld(qtbot):
    fake = FakeMS(udn="uuid:fake", name="Meine Musik (Raumfeld)\u202e", raumfeld=False)
    w, _, _ = window(qtbot, [FakeMS(), fake])
    combo = w.findChild(QComboBox, "source")
    assert combo.itemText(0) == "Meine Musik (Raumfeld)"
    assert combo.itemText(1) == "DLNA: Meine Musik (Raumfeld)"


def test_foreign_source_covers_only_from_its_own_host(qtbot):
    class Covers:
        from PySide6.QtCore import QObject as _Q
        def __init__(self):
            self.calls = []
            from PySide6.QtCore import QObject, Signal

            class _S(QObject):
                loaded = Signal(str, object)
            self._s = _S()
            self.loaded = self._s.loaded

        def drop_queued(self):
            pass

        def request(self, key, url, artist=None, album=None, size=48, server_host=None, strict=False):
            self.calls.append((url, server_host, strict))
            return None

    cov = Covers()
    ms = FakeMS(raumfeld=False, name="DS923")
    ms.browse = lambda *a, **k: Page([MediaContainer("0/a", "A", "B", 1, "http://192.168.0.50/x.jpg", "album")], 1, 0)
    w = MusicWindow([ms], cov, default_config(), save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    assert cov.calls and cov.calls[0][2] is True


# --- Final-Review-Fixes V2 -------------------------------------------------
def test_artist_paging_counts_raw_items_no_duplicates(qtbot):
    ms = FakeMS()
    albums = [MediaContainer(f"0/al/{i}", f"Alb{i}", "X", 1, None, "album") for i in range(40)]
    folder = MediaContainer("0/all", "All Tracks by X", None, 9, None, "folder")
    raw = [folder] + albums

    def browse(object_id, start=0, count=100):
        ms.calls.append(("browse", object_id, start))
        if object_id == ARTISTS:
            return Page([MediaContainer("0/ar/X", "X", None, 2, None, "artist")], 1, 0)
        return Page(raw[start:start + 20], len(raw), start)

    ms.browse = browse
    w, _, _ = window(qtbot, [ms])
    w.resize(560, 300)
    w.show_artists()
    qtbot.mouseClick(btn(w, "open:0"), Qt.LeftButton)
    for _ in range(5):
        w.scroll_to_end()
        qtbot.wait(20)
    titles = [lbl(w, f"title:{i}").text() for i in range(60) if lbl(w, f"title:{i}") is not None]
    assert len(titles) == 40 and len(set(titles)) == 40
    starts = [s for c, oid, s in ms.calls if c == "browse" and oid == "0/ar/X"]
    assert starts == [0, 20, 40][:len(starts)] and 20 in starts


def test_short_first_page_keeps_loading_without_scrollbar(qtbot):
    ms = FakeMS(total=60)
    w, _, _ = window(qtbot, [ms])
    w.resize(560, 4000)
    w.show()
    qtbot.waitUntil(lambda: ("browse", ALBUMS, 20) in ms.calls, timeout=2000)


def test_play_not_sent_without_metadata(qtbot):
    ms = FakeMS()

    def boom(object_id):
        raise UpnpError("One M startet neu")

    ms.metadata = boom
    w, _, _ = window(qtbot, [ms])
    emitted = []
    w.play.connect(lambda *a: emitted.append(a))
    qtbot.mouseClick(btn(w, "play:0"), Qt.LeftButton)
    assert emitted == [] and lbl(w, "status").text() == "Quelle nicht erreichbar"


def test_no_target_hint_in_music_window(qtbot):
    cfg = default_config()
    w = MusicWindow([FakeMS()], None, cfg, save=lambda c: None, run_async=False, target=lambda: None)
    qtbot.addWidget(w)
    w.show_albums()
    emitted = []
    w.play.connect(lambda *a: emitted.append(a))
    qtbot.mouseClick(btn(w, "play:0"), Qt.LeftButton)
    assert emitted == [] and "Ziel-Raum" in lbl(w, "status").text()



def test_music_starts_with_artists(qtbot):
    cfg = default_config()
    cfg.target_room = "uuid:bad"
    w = MusicWindow([FakeMS()], None, cfg, save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    assert lbl(w, "heading").text() == "Künstler"
    assert lbl(w, "title:0").text() == "Naked Lunch"


def test_no_redundant_play_all_button(qtbot):
    w, _, _ = window(qtbot)
    qtbot.mouseClick(btn(w, "open:0"), Qt.LeftButton)
    assert btn(w, "play_album") is None


class ArtistNameMS(FakeMS):
    def browse(self, object_id, start=0, count=100):
        if object_id == ARTISTS:
            return Page([MediaContainer("0/My Music/Artists/A", "Die Sterne", "Die Sterne", 2, None, "artist")], 1, 0)
        return super().browse(object_id, start, count)


def test_artist_name_not_doubled(qtbot):
    cfg = default_config()
    w = MusicWindow([ArtistNameMS()], None, cfg, save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    assert lbl(w, "title:0").text() == "Die Sterne"


def test_view_change_drops_queued_covers(qtbot):
    class Covers:
        dropped = 0
        loaded = type("S", (), {"connect": lambda self, f: None})()

        def request(self, *a, **k):
            return None

        def drop_queued(self):
            Covers.dropped += 1

    cfg = default_config()
    w = MusicWindow([FakeMS()], Covers(), cfg, save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    before = Covers.dropped
    w.show_albums()
    assert Covers.dropped > before


def test_artist_play_shuffles_all_tracks(qtbot):
    cfg = default_config()
    cfg.target_room = "uuid:bad"
    w = MusicWindow([FakeMS()], None, cfg, save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    normal = []
    w.play.connect(lambda *a: normal.append(a))
    with qtbot.waitSignal(w.play_shuffled) as blocker:
        qtbot.mouseClick(btn(w, "play:0"), Qt.LeftButton)
    uri = blocker.args[0]
    assert "cid=0%2FMy%20Music%2FArtists%2FX%2FAllTracks" in uri
    assert normal == []


def test_foreign_source_artist_has_no_play_button(qtbot):
    ms = FakeMS(raumfeld=False, name="DS923")
    ms.browse = lambda *a, **k: Page([MediaContainer("0/ar/1", "Die Sterne", None, 2, None, "artist")], 1, 0)
    w = MusicWindow([ms], None, default_config(), save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    assert lbl(w, "title:0") is not None and btn(w, "play:0") is None


class ArtistSearchMS(FakeMS):
    def search(self, container_id, text, start=0, count=100):
        self.calls.append(("search", container_id, text))
        if container_id == "0/My Music/Search/TrackArtists":
            return Page([MediaContainer("0/My Music/Search/TrackArtists/Die Sterne", "Die Sterne", "Die Sterne", 1, None, "artist")], 1, 0)
        return super().search(container_id, text, start, count)


def test_artist_view_searches_artists(qtbot):
    ms = ArtistSearchMS()
    cfg = default_config()
    cfg.target_room = "uuid:bad"
    w = MusicWindow([ms], None, cfg, save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    assert w.query.placeholderText() == "Künstler suchen …"
    w.search_now("band")
    assert ("search", "0/My Music/Search/TrackArtists", "band") in ms.calls
    assert w._stack[-1].items[0].id == "0/My Music/Artists/Die Sterne"  # Treffer zeigt aufs normale Künstler-Verzeichnis
    with qtbot.waitSignal(w.play_shuffled) as blocker:
        qtbot.mouseClick(btn(w, "play:0"), Qt.LeftButton)
    assert "cid=0%2FMy%20Music%2FArtists%2FDie%20Sterne%2FAllTracks" in blocker.args[0]


def test_album_view_still_searches_albums(qtbot):
    ms = ArtistSearchMS()
    w = MusicWindow([ms], None, default_config(), save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    w.show_albums()
    assert w.query.placeholderText() == "Album suchen …"
    w.search_now("love")
    assert ("search", SEARCH_ALBUMS, "love") in ms.calls


def test_query_cleared_only_when_search_kind_changes(qtbot):
    ms = ArtistSearchMS()
    w = MusicWindow([ms], None, default_config(), save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    w.query.setText("band")
    w.show_artists()  # gleiche Suchart: bleibt
    assert w.query.text() == "band"
    w.show_albums()  # Künstler -> Alben: geleert
    assert w.query.text() == ""
    w.query.setText("love")
    w.show_folders()  # Alben -> Ordner sucht weiter Alben: bleibt
    assert w.query.text() == "love"
    w.show_artists()
    assert w.query.text() == ""
    assert not w._timer.isActive()  # Leeren löst keine neue Suche aus



ALBUM_ART = "http://192.168.0.176:47366/?albumArtist=Naked%20Lunch&album=Album%2001"


def fav_window(qtbot, servers=None):
    cfg = default_config()
    cfg.target_room = "uuid:bad"
    saved, changed = [], []
    w = MusicWindow(servers or [FakeMS()], None, cfg, save=saved.append, run_async=False)
    qtbot.addWidget(w)
    w.favorites_changed.connect(lambda: changed.append(1))
    return w, cfg, saved, changed


def test_star_album_saves_music_favorite(qtbot):
    w, cfg, saved, changed = fav_window(qtbot)
    w.show_albums()
    before = len(cfg.favorites)
    qtbot.mouseClick(btn(w, "star:0"), Qt.LeftButton)
    fav = cfg.favorites[-1]
    assert len(cfg.favorites) == before + 1
    assert (fav.kind, fav.name) == ("album", "Album 01 · Naked Lunch")
    assert fav.url == container_uri(UDN, FIRST_ALBUM, 0)
    assert fav.meta == f"<DIDL-Lite id='{FIRST_ALBUM}'/>"
    assert fav.cover == ALBUM_ART
    assert saved and changed and btn(w, "star:0").text() == "★"


def test_star_track_saves_start_index(qtbot):
    w, cfg, saved, changed = fav_window(qtbot)
    w.show_albums()
    qtbot.mouseClick(btn(w, "open:0"), Qt.LeftButton)
    qtbot.mouseClick(btn(w, "startrack:2"), Qt.LeftButton)
    fav = cfg.favorites[-1]
    assert fav.kind == "track" and fav.url == container_uri(UDN, FIRST_ALBUM, 2)
    assert fav.name.startswith("Titel 05") and fav.cover == ALBUM_ART


def test_star_artist_saves_alltracks_with_count(qtbot):
    w, cfg, saved, changed = fav_window(qtbot)
    qtbot.mouseClick(btn(w, "star:0"), Qt.LeftButton)  # Start = Künstler
    fav = cfg.favorites[-1]
    assert (fav.kind, fav.name) == ("artist", "Naked Lunch · Zufall")
    assert fav.url == container_uri(UDN, "0/My Music/Artists/X/AllTracks", 0)
    assert fav.count == 20  # FakeMS.browse liefert total=20 für unbekannte IDs


def test_star_twice_adds_once(qtbot):
    w, cfg, saved, changed = fav_window(qtbot)
    w.show_albums()
    qtbot.mouseClick(btn(w, "star:0"), Qt.LeftButton)
    n = len(cfg.favorites)
    qtbot.mouseClick(btn(w, "star:0"), Qt.LeftButton)
    assert len(cfg.favorites) == n


def test_existing_favorite_shows_filled_star(qtbot):
    from deskremote.core.config import Favorite
    cfg = default_config()
    cfg.favorites.append(Favorite("x", container_uri(UDN, FIRST_ALBUM, 0), kind="album"))
    w = MusicWindow([FakeMS()], None, cfg, save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    w.show_albums()
    assert btn(w, "star:0").text() == "★" and btn(w, "star:1").text() == "☆"


def test_foreign_source_has_no_star(qtbot):
    w, cfg, saved, changed = fav_window(qtbot, [FakeMS(raumfeld=False, name="DS923")])
    w.show_albums()
    assert lbl(w, "title:0") is not None and btn(w, "star:0") is None


def test_star_without_metadata_saves_nothing(qtbot):
    ms = FakeMS()

    def broken(object_id):
        raise UpnpError("weg")

    ms.metadata = broken
    w, cfg, saved, changed = fav_window(qtbot, [ms])
    w.show_albums()
    before = list(cfg.favorites)
    qtbot.mouseClick(btn(w, "star:0"), Qt.LeftButton)
    assert cfg.favorites == before and not saved and not changed
    assert lbl(w, "status").text() == "Quelle nicht erreichbar"



def test_late_favorite_result_marks_only_matching_star(qtbot):
    """Fix 3: Ergebnis kommt nach Ansichtswechsel – nur der passende Stern wird ★."""
    from deskremote.core.config import Favorite
    w, cfg, saved, changed = fav_window(qtbot)  # Start: Künstler, star:0 = Künstler
    late = Favorite("Album 01 · Naked Lunch", container_uri(UDN, FIRST_ALBUM, 0), None, "album", "<DIDL-Lite/>")
    w._on_fav_ready(late, "star:0")
    assert btn(w, "star:0").text() == "☆"
    w.show_albums()
    assert btn(w, "star:0").text() == "★"
