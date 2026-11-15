from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QListWidget, QPushButton

from deskremote.core.anlage import RoomState
from deskremote.core.config import Favorite, default_config
from deskremote.core.radio import SearchError, Station
from deskremote.ui.couple import CoupleDialog
from deskremote.ui.search import SearchWindow
from deskremote.ui.settings import SettingsWindow

KRONE = Station("1", "KroneHit Radio", "http://onair-ha1.krone.at/kronehit64.aac", "AAC", 163, 2295, "AT")


class FakeRadio:
    def __init__(self, result=None, error=False):
        self.result = result if result is not None else [KRONE]
        self.error = error
        self.queries = []

    def search(self, query, country="AT", limit=30):
        self.queries.append((query, country))
        if self.error:
            raise SearchError("Senderverzeichnis nicht erreichbar")
        return self.result

    @staticmethod
    def to_favorite(st):
        return Favorite(st.name, st.url)


def search_window(qtbot, radio, saved=None):
    cfg = default_config()
    w = SearchWindow(radio, cfg, save=(saved.append if saved is not None else lambda c: None), run_async=False)
    qtbot.addWidget(w)
    return w, cfg


def test_search_uses_country_filter(qtbot):
    radio = FakeRadio()
    w, cfg = search_window(qtbot, radio)
    w.search_now("kronehit")
    assert radio.queries == [("kronehit", "AT")]
    w.set_country(None)
    w.search_now("kronehit")
    assert radio.queries[-1] == ("kronehit", None)


def test_search_play_emits_favorite(qtbot):
    w, cfg = search_window(qtbot, FakeRadio())
    w.search_now("kronehit")
    with qtbot.waitSignal(w.play) as blocker:
        qtbot.mouseClick(w.findChild(QPushButton, "play:0"), Qt.LeftButton)
    assert blocker.args == [Favorite("KroneHit Radio", KRONE.url)]


def test_search_star_adds_favorite_and_saves(qtbot):
    saved = []
    w, cfg = search_window(qtbot, FakeRadio(), saved)
    w.search_now("kronehit")
    with qtbot.waitSignal(w.favorites_changed):
        qtbot.mouseClick(w.findChild(QPushButton, "star:0"), Qt.LeftButton)
    assert cfg.favorites[-1] == Favorite("KroneHit Radio", KRONE.url, "")
    assert saved and saved[-1] is cfg


def test_search_star_does_not_duplicate(qtbot):
    w, cfg = search_window(qtbot, FakeRadio())
    w.search_now("kronehit")
    btn = w.findChild(QPushButton, "star:0")
    qtbot.mouseClick(btn, Qt.LeftButton)
    qtbot.mouseClick(btn, Qt.LeftButton)
    assert sum(1 for f in cfg.favorites if f.url == KRONE.url) == 1


def test_search_error_shows_message(qtbot):
    w, cfg = search_window(qtbot, FakeRadio(error=True))
    w.search_now("x")
    assert w.findChild(QLabel, "status").text() == "Senderverzeichnis nicht erreichbar"


def test_search_empty_result_message(qtbot):
    w, cfg = search_window(qtbot, FakeRadio(result=[]))
    w.search_now("gibtsnicht")
    assert w.findChild(QLabel, "status").text() == "Keine Sender gefunden"


def settings_window(qtbot, saved):
    cfg = default_config()
    w = SettingsWindow(cfg, save=saved.append)
    qtbot.addWidget(w)
    return w, cfg


def test_settings_manual_url_adds_favorite(qtbot):
    saved = []
    w, cfg = settings_window(qtbot, saved)
    assert w.add_favorite("Ö3", "https://orf-live.ors-shoutcast.at/oe3-q2a") is True
    assert w.add_favorite("kaputt", "ftp://x") is False
    with qtbot.waitSignal(w.config_changed):
        qtbot.mouseClick(w.findChild(QPushButton, "save"), Qt.LeftButton)
    assert cfg.favorites[-1] == Favorite("Ö3", "https://orf-live.ors-shoutcast.at/oe3-q2a")
    assert saved and saved[-1] is cfg


def test_settings_reorder_rename_delete(qtbot):
    saved = []
    w, cfg = settings_window(qtbot, saved)
    lst = w.findChild(QListWidget, "favorites")
    lst.setCurrentRow(2)
    qtbot.mouseClick(w.findChild(QPushButton, "up"), Qt.LeftButton)
    w.rename_current("Krone")
    lst.setCurrentRow(0)
    qtbot.mouseClick(w.findChild(QPushButton, "delete"), Qt.LeftButton)
    qtbot.mouseClick(w.findChild(QPushButton, "save"), Qt.LeftButton)
    assert [f.name for f in cfg.favorites] == ["Krone", "Ö1"]


def test_settings_cancel_keeps_config(qtbot):
    saved = []
    w, cfg = settings_window(qtbot, saved)
    w.add_favorite("X", "http://x")
    w.close()
    assert [f.name for f in cfg.favorites] == ["FM4", "Ö1", "Kronehit"]
    assert saved == []


def test_settings_hotkey_change(qtbot):
    saved = []
    w, cfg = settings_window(qtbot, saved)
    w.set_hotkey("standby", "Ctrl+Alt+S")
    qtbot.mouseClick(w.findChild(QPushButton, "save"), Qt.LeftButton)
    assert cfg.hotkeys["standby"] == "Ctrl+Alt+S"


ROOMS = [
    RoomState("uuid:bad", "Badezimmer", "uuid:z1", "ACTIVE", True, None, 30, False),
    RoomState("uuid:kueche", "Küche", "uuid:z1", "ACTIVE", True, None, 30, False),
    RoomState("uuid:wohn", "Wohnzimmer", None, "ACTIVE", False, None, 30, False),
]





def test_search_unexpected_error_shows_message(qtbot):
    class Broken(FakeRadio):
        def search(self, query, country="AT", limit=30):
            raise RuntimeError("unerwartet")

    w, cfg = search_window(qtbot, Broken())
    w.search_now("x")
    assert w.findChild(QLabel, "status").text() == "Suche fehlgeschlagen"


def test_settings_emits_closed(qtbot):
    saved = []
    w, cfg = settings_window(qtbot, saved)
    w.show()
    with qtbot.waitSignal(w.closed, timeout=1000):
        w.close()


def test_dialog_labels_are_plain_text(qtbot):
    from PySide6.QtCore import Qt as _Qt
    evil = Station("9", "FM4 <img src='file://203.0.113.5/s/a.png'>", "http://x", "MP3", 128, 1, "AT")
    w, cfg = search_window(qtbot, FakeRadio(result=[evil]))
    w.search_now("fm4")
    s = SettingsWindow(default_config(), save=lambda c: None)
    c = CoupleDialog([RoomState("uuid:a", "<b>A</b>", None, "ACTIVE", False, None, 1, False)], None)
    for win in (w, s, c):
        qtbot.addWidget(win)
        labels = win.findChildren(QLabel)
        assert labels and all(l.textFormat() == _Qt.PlainText for l in labels), win


def test_settings_title_and_layout(qtbot):
    saved = []
    w, cfg = settings_window(qtbot, saved)
    from PySide6.QtWidgets import QLineEdit
    w.findChild(QLineEdit, "title").setText("Zuhause")
    qtbot.mouseClick(w.findChild(QPushButton, "save"), Qt.LeftButton)
    assert cfg.title == "Zuhause"
    assert w.findChild(QListWidget, "favorites").minimumHeight() >= 220


def test_windows_have_app_icon(qtbot):
    s = SettingsWindow(default_config(), save=lambda c: None)
    w, cfg = search_window(qtbot, FakeRadio())
    c = CoupleDialog(ROOMS, None)
    for win in (s, w, c):
        qtbot.addWidget(win)
        assert not win.windowIcon().isNull()


def couple_dialog(qtbot, rooms=ROOMS, target="uuid:bad"):
    d = CoupleDialog(rooms, target)
    qtbot.addWidget(d)
    return d


def box(d, udn):
    return d.findChild(QCheckBox, f"couple:{udn}")


def test_couple_dialog_prechecks_current_group(qtbot):
    d = couple_dialog(qtbot)
    assert box(d, "uuid:bad").isChecked() and box(d, "uuid:kueche").isChecked()
    assert not box(d, "uuid:wohn").isChecked()
    assert d.findChild(QLabel, "status").text() == "Gekoppelt: Badezimmer + Küche"
    assert not d.findChild(QPushButton, "apply").isEnabled()  # nichts geändert


def test_couple_dialog_adds_room_and_stays_open(qtbot):
    d = couple_dialog(qtbot)
    d.show()
    box(d, "uuid:wohn").setChecked(True)
    actions = []
    d.action.connect(lambda a, args: actions.append((a, args)))
    qtbot.mouseClick(d.findChild(QPushButton, "apply"), Qt.LeftButton)
    assert actions == [("couple", (["uuid:bad", "uuid:kueche", "uuid:wohn"],))]
    assert d.isVisible()
    assert d.findChild(QLabel, "status").text() == "Wird übernommen …"


def test_couple_dialog_unchecking_drops_rooms(qtbot):
    d = couple_dialog(qtbot)
    box(d, "uuid:kueche").setChecked(False)
    actions = []
    d.action.connect(lambda a, args: actions.append((a, args)))
    qtbot.mouseClick(d.findChild(QPushButton, "apply"), Qt.LeftButton)
    assert actions == [("uncouple", ("uuid:kueche",))]
    # wie die Teufel-App: der letzte Raum behält die Zone (und damit eine evtl. Spotify-Markierung)


def test_couple_dialog_new_group_needs_two(qtbot):
    alone = [RoomState(u, n, None, "ACTIVE", False, None, 1, False)
             for u, n in (("uuid:a", "A"), ("uuid:b", "B"), ("uuid:c", "C"))]
    d = couple_dialog(qtbot, alone, None)
    assert d.findChild(QLabel, "status").text() == "Keine Räume gekoppelt"
    box(d, "uuid:a").setChecked(True)
    assert not d.findChild(QPushButton, "apply").isEnabled()
    box(d, "uuid:b").setChecked(True)
    assert d.findChild(QPushButton, "apply").isEnabled()


def test_couple_dialog_updates_from_live_state(qtbot):
    d = couple_dialog(qtbot)
    box(d, "uuid:wohn").setChecked(True)
    qtbot.mouseClick(d.findChild(QPushButton, "apply"), Qt.LeftButton)
    all_three = [RoomState(r.udn, r.name, "uuid:z1", "ACTIVE", True, None, 30, False) for r in ROOMS]
    d.set_rooms(all_three)
    assert d.findChild(QLabel, "status").text() == "Gekoppelt: Badezimmer + Küche + Wohnzimmer"
    assert all(box(d, r.udn).isChecked() for r in ROOMS)


def test_checked_box_shows_white_checkmark(qtbot):
    import os
    import re
    d = couple_dialog(qtbot)
    m = re.search(r"QCheckBox::indicator:checked \{[^}]*image: url\(([^)]+)\)", d.styleSheet())
    assert m and os.path.exists(m.group(1))


def test_checkmark_file_is_private_per_process(qtbot, tmp_path, monkeypatch):
    import re
    import tempfile
    from deskremote.ui import theme
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(theme, "_CHECK_PATH", None)
    planted = tmp_path / "deskremote-check.png"
    planted.write_bytes(b"fremd")
    url = re.search(r"url\(([^)]+)\)", theme.checkbox_style()).group(1)
    assert url != planted.as_posix() and "deskremote-" in url
    assert re.search(r"url\(([^)]+)\)", theme.checkbox_style()).group(1) == url  # einmal pro Prozess



def test_star_while_settings_open_survives_settings_save(qtbot):
    """Bug v1.0.0: Einstellungen schrieben ihre alte Favoriten-Kopie zurück."""
    cfg = default_config()
    saved = []
    settings = SettingsWindow(cfg, save=saved.append)
    search = SearchWindow(FakeRadio(), cfg, save=saved.append, run_async=False)
    qtbot.addWidget(settings)
    qtbot.addWidget(search)
    search.favorites_changed.connect(settings.sync_new_favorites)  # so verdrahtet es die App
    search.search_now("kronehit")
    qtbot.mouseClick(search.findChild(QPushButton, "star:0"), Qt.LeftButton)
    assert any(KRONE.name in settings.list.item(i).text() for i in range(settings.list.count()))
    qtbot.mouseClick(settings.findChild(QPushButton, "save"), Qt.LeftButton)
    assert any(f.url == KRONE.url for f in cfg.favorites)


def test_sync_keeps_unsaved_edits_in_settings(qtbot):
    from deskremote.core.config import Favorite
    cfg = default_config()
    settings = SettingsWindow(cfg, save=lambda c: None)
    qtbot.addWidget(settings)
    settings.list.setCurrentRow(0)
    settings.rename_current("FM4 neu")
    cfg.favorites.append(Favorite("Neu", "http://neu"))
    settings.sync_new_favorites()
    names = [f.name for f in settings._favs]
    assert names[0] == "FM4 neu" and names[-1] == "Neu"


def test_periodic_refresh_keeps_unsaved_checks(qtbot):
    """Bug v1.0.3: die 5-s-Aktualisierung setzte frisch gesetzte Haken zurück."""
    d = couple_dialog(qtbot)
    box(d, "uuid:wohn").setChecked(True)
    d.set_rooms(ROOMS)  # unveränderter Stand von der Anlage
    assert box(d, "uuid:wohn").isChecked()
    assert d.findChild(QPushButton, "apply").isEnabled()


def test_confirmed_state_after_apply_resets_checks(qtbot):
    d = couple_dialog(qtbot)
    box(d, "uuid:wohn").setChecked(True)
    qtbot.mouseClick(d.findChild(QPushButton, "apply"), Qt.LeftButton)
    d.set_rooms(ROOMS)  # Anlage hat (noch) nicht gekoppelt → wieder echter Stand
    assert not box(d, "uuid:wohn").isChecked()


def test_user_can_undo_edit_back_to_current(qtbot):
    d = couple_dialog(qtbot)
    box(d, "uuid:wohn").setChecked(True)
    box(d, "uuid:wohn").setChecked(False)  # wieder wie vorher → nichts zu übernehmen
    d.set_rooms(ROOMS)
    assert not d.findChild(QPushButton, "apply").isEnabled()


def test_settings_online_covers_checkbox(qtbot):
    saved = []
    w, cfg = settings_window(qtbot, saved)
    box = w.findChild(QCheckBox, "online_covers")
    assert box.isChecked() is True
    box.setChecked(False)
    qtbot.mouseClick(w.findChild(QPushButton, "save"), Qt.LeftButton)
    assert cfg.online_covers is False


def test_star_keeps_logo(qtbot):
    st = Station("7", "Radio Logo", "http://logo.example/stream", "MP3", 128, 1, "AT", favicon="https://logo.example/l.png")
    w, cfg = search_window(qtbot, FakeRadio(result=[st]))
    w.search_now("logo")
    qtbot.mouseClick(w.findChild(QPushButton, "star:0"), Qt.LeftButton)
    assert cfg.favorites[-1].logo == "https://logo.example/l.png"


def test_settings_save_keeps_logos(qtbot):
    saved = []
    w, cfg = settings_window(qtbot, saved)
    cfg.favorites[0].logo = "https://fm4.orf.at/logo.png"
    w2 = SettingsWindow(cfg, save=saved.append)
    qtbot.addWidget(w2)
    qtbot.mouseClick(w2.findChild(QPushButton, "save"), Qt.LeftButton)
    assert cfg.favorites[0].logo == "https://fm4.orf.at/logo.png"
    from deskremote.core.config import Favorite as F
    cfg.favorites.append(F("Neu", "http://neu", "https://neu.example/l.png"))
    w2.sync_new_favorites()
    assert w2._favs[-1].logo == "https://neu.example/l.png"



MUSIC_FAV = Favorite("Album 22 · Die Sterne", "dlna-playcontainer://x?cid=c&fii=0", kind="album",
                     meta="<DIDL-Lite/>", cover="http://192.168.0.176:47366/?a", count=0)


def test_settings_keep_music_fields_on_save(qtbot):
    from dataclasses import replace
    saved = []
    w, cfg = settings_window(qtbot, saved)
    cfg.favorites.append(replace(MUSIC_FAV))
    w2 = SettingsWindow(cfg, save=saved.append)
    qtbot.addWidget(w2)
    w2.findChild(QListWidget, "favorites").setCurrentRow(3)
    w2.rename_current("Mondsafari")
    qtbot.mouseClick(w2.findChild(QPushButton, "save"), Qt.LeftButton)
    assert cfg.favorites[3] == replace(MUSIC_FAV, name="Mondsafari")


def test_settings_list_shows_kind_not_uri(qtbot):
    from dataclasses import replace
    cfg = default_config()
    cfg.favorites.append(replace(MUSIC_FAV))
    w = SettingsWindow(cfg, save=lambda c: None)
    qtbot.addWidget(w)
    text = w.findChild(QListWidget, "favorites").item(3).text()
    assert text == "Album 22 · Die Sterne    —    Album" and "dlna-playcontainer" not in text


def test_sync_new_favorites_keeps_music_fields(qtbot):
    from dataclasses import replace
    saved = []
    w, cfg = settings_window(qtbot, saved)
    cfg.favorites.append(replace(MUSIC_FAV))
    w.sync_new_favorites()
    qtbot.mouseClick(w.findChild(QPushButton, "save"), Qt.LeftButton)
    assert cfg.favorites[-1] == MUSIC_FAV



def test_sync_keeps_track_favorite_with_same_url_as_album(qtbot):
    """Fix 4: Titel 1 hat dieselbe Adresse wie das Album – darf beim Sync nicht verloren gehen."""
    from dataclasses import replace
    saved = []
    cfg = default_config()
    cfg.favorites.append(replace(MUSIC_FAV))
    w = SettingsWindow(cfg, save=saved.append)
    qtbot.addWidget(w)
    track = replace(MUSIC_FAV, name="Titel 1 · Die Sterne", kind="track")
    cfg.favorites.append(track)
    w.sync_new_favorites()
    qtbot.mouseClick(w.findChild(QPushButton, "save"), Qt.LeftButton)
    assert cfg.favorites[-1] == track



def test_settings_checkbox_has_white_check(qtbot):
    """Abnahme v2.2: auch in den Einstellungen weißer Haken im roten Kästchen (wie beim Koppeln)."""
    from deskremote.ui import theme
    w = SettingsWindow(default_config(), save=lambda c: None)
    qtbot.addWidget(w)
    assert theme.checkbox_style() in w.styleSheet()



def test_search_marks_hls_and_disables_buttons(qtbot):
    bbc = Station("2", "BBC Radio 6 Music", "http://x/bbc_6music.m3u8", "AAC", 320, 9, "GB", hls=True)
    w, cfg = search_window(qtbot, FakeRadio([KRONE, bbc]))
    w.search_now("bbc")
    info = w.findChild(QLabel, "info:1")
    assert "spielt auf Raumfeld nicht" in info.text()
    assert not w.findChild(QPushButton, "play:1").isEnabled()
    assert not w.findChild(QPushButton, "star:1").isEnabled()
    assert w.findChild(QPushButton, "play:0").isEnabled()
    assert "spielt auf Raumfeld nicht" not in w.findChild(QLabel, "info:0").text()



def test_couple_dialog_dissolving_keeps_target_in_zone(qtbot):
    """Alles abgehakt: nur die anderen Räume verlassen die Zone – sonst gibt es evtl. gar keine Zone
    und damit kein Spotify-Gerät mehr (Teufel-App lässt dann nichts mehr ankreuzen)."""
    d = couple_dialog(qtbot, target="uuid:kueche")
    box(d, "uuid:bad").setChecked(False)
    box(d, "uuid:kueche").setChecked(False)
    actions = []
    d.action.connect(lambda a, args: actions.append((a, args)))
    qtbot.mouseClick(d.findChild(QPushButton, "apply"), Qt.LeftButton)
    assert actions == [("uncouple", ("uuid:bad",))]
