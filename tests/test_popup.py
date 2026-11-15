import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QSlider

from deskremote.core.anlage import RoomState
from deskremote.core.config import default_config
from deskremote.ui.popup import Popup
from deskremote.ui.theme import STYLESHEET, tray_icon

WOHN, BAD, KUECHE = "uuid:wohn", "uuid:bad", "uuid:kueche"


def rooms(grouped=False):
    zb = "uuid:zbad"
    ze = zb if grouped else "uuid:zkueche"
    return [
        RoomState(BAD, "Badezimmer", zb, "ACTIVE", True, "FM4", 34, False),
        RoomState(KUECHE, "Küche", ze, "AUTOMATIC_STANDBY", grouped, "FM4" if grouped else None, 31, False),
        RoomState(WOHN, "Wohnzimmer", None, "ACTIVE", False, None, 31, True),
    ]


def popup(qtbot, grouped=False):
    cfg = default_config()
    cfg.target_room = WOHN
    p = Popup(cfg)
    qtbot.addWidget(p)
    p.set_online(True)
    p.set_state(rooms(grouped))
    return p, cfg


def btn(p, name):
    return p.findChild(QPushButton, name)


def head(p, udn):
    """Kopfzeile eines Raums oder einer Gruppe (ein Label für beides seit v2.2)."""
    return p.findChild(QLabel, f"target:{udn}")


def shown(label):
    """Sichtbarer Text einer formatierten Kopfzeile."""
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setHtml(label.text())
    return doc.toPlainText().replace("\xa0", " ").replace("\ufffc", "[Icon]")


def test_popup_renders_rooms(qtbot):
    p, _ = popup(qtbot)
    assert shown(head(p, BAD)) == "Badezimmer · FM4"
    assert shown(head(p, KUECHE)) == "Küche"  # nichts läuft: kein Trennpunkt
    assert shown(head(p, WOHN)) == "Wohnzimmer · [Icon] Spotify"
    assert "data:image/png" in head(p, WOHN).text()


def test_click_favorite_emits_play_on_target(qtbot):
    p, cfg = popup(qtbot)
    with qtbot.waitSignal(p.action) as blocker:
        qtbot.mouseClick(btn(p, "fav:0"), Qt.LeftButton)
    assert blocker.args == ["play_favorite", (WOHN, cfg.favorites[0])]


def test_click_room_changes_target(qtbot):
    p, cfg = popup(qtbot)
    with qtbot.waitSignal(p.target_changed) as blocker:
        qtbot.mouseClick(head(p, BAD), Qt.LeftButton)
    assert blocker.args == [BAD]
    assert cfg.target_room == BAD
    assert head(p, BAD).property("target") is True
    assert "#E2001A" in head(p, BAD).text()


def test_slider_emits_set_volume(qtbot):
    p, _ = popup(qtbot)
    slider = p.findChild(QSlider, f"vol:{BAD}")
    assert slider.value() == 34
    with qtbot.waitSignal(p.action) as blocker:
        slider.setValue(50)
    assert blocker.args == ["set_volume", (BAD, 50)]


def test_set_state_does_not_emit_volume(qtbot):
    p, _ = popup(qtbot)
    emitted = []
    p.action.connect(lambda *a: emitted.append(a))
    p.set_state(rooms())
    assert emitted == []


def test_standby_button_toggles(qtbot):
    p, _ = popup(qtbot)
    with qtbot.waitSignal(p.action) as blocker:
        qtbot.mouseClick(btn(p, f"standby:{KUECHE}"), Qt.LeftButton)
    assert blocker.args == ["wake", (KUECHE,)]
    with qtbot.waitSignal(p.action) as blocker:
        qtbot.mouseClick(btn(p, f"standby:{BAD}"), Qt.LeftButton)
    assert blocker.args == ["standby", (BAD,)]


def test_offline_disables_controls(qtbot):
    p, _ = popup(qtbot)
    p.set_online(False)
    assert not btn(p, "fav:0").isEnabled()
    assert not p.findChild(QSlider, f"vol:{BAD}").isEnabled()
    assert p.findChild(QLabel, "offline").isVisible() or not p.isVisible()
    assert p.findChild(QLabel, "offline").text() == "Anlage nicht erreichbar"


def test_grouped_rooms_share_play_button(qtbot):
    p, _ = popup(qtbot, grouped=True)
    assert btn(p, "play:uuid:zbad") is not None
    assert btn(p, f"play:{BAD}") is None and btn(p, f"play:{KUECHE}") is None
    with qtbot.waitSignal(p.action) as blocker:
        qtbot.mouseClick(btn(p, "play:uuid:zbad"), Qt.LeftButton)
    assert blocker.args[0] == "toggle_play"


def test_single_room_play_and_unassigned_disabled(qtbot):
    p, _ = popup(qtbot)
    assert btn(p, "play:uuid:zbad") is not None
    assert not btn(p, f"play:{WOHN}").isEnabled()


def test_theme_colors_and_icon(qtbot):
    assert "#000000" in STYLESHEET and "#E2001A" in STYLESHEET and "#FFFFFF" in STYLESHEET
    assert not tray_icon(True).isNull() and not tray_icon(False).isNull()


def test_same_state_does_not_rebuild(qtbot):
    p, _ = popup(qtbot)
    before = head(p, BAD)
    p.set_state(rooms())
    assert head(p, BAD) is before


def test_tray_click_right_after_autohide_does_not_reopen(qtbot):
    from PySide6.QtWidgets import QSystemTrayIcon
    from deskremote.ui.tray import Tray
    p, cfg = popup(qtbot)
    t = Tray(p, cfg)
    p.show()
    p.hide()
    p.mark_auto_hidden()
    t._on_activated(QSystemTrayIcon.Trigger)
    assert not p.isVisible()


EVIL = "FM4 <img src='file://203.0.113.5/s/a.png'>"


def test_external_text_is_never_rich_text(qtbot):
    cfg = default_config()
    p = Popup(cfg)
    qtbot.addWidget(p)
    p.set_online(True)
    evil = [RoomState(BAD, "<b>Bad</b>", "uuid:z", "ACTIVE", True, EVIL, 30, False)]
    cfg.target_room = BAD
    p.set_state(evil)
    labels = p.findChildren(QLabel)
    # einzige formatierte Labels: Kopfzeilen mit vollständig maskiertem Text
    assert labels and all(l.textFormat() == Qt.PlainText or l.objectName().startswith("target:") for l in labels)
    assert shown(head(p, BAD)) == f"<b>Bad</b> · {EVIL}"
    assert "<b>" not in head(p, BAD).text()


def test_play_click_flips_icon_immediately(qtbot):
    from deskremote.ui.theme import ICON_PAUSE, ICON_PLAY
    p, _ = popup(qtbot)
    b = btn(p, "play:uuid:zkueche")
    assert b.text() == ICON_PLAY
    qtbot.mouseClick(b, Qt.LeftButton)
    assert b.text() == ICON_PAUSE


def test_favorites_wrap_after_three(qtbot):
    from deskremote.core.config import Favorite
    cfg = default_config()
    cfg.favorites += [Favorite("A", "http://a"), Favorite("B", "http://b")]
    p = Popup(cfg)
    qtbot.addWidget(p)
    grid = p._fav_grid
    pos = {grid.itemAt(i).widget().objectName(): grid.getItemPosition(i)[:2] for i in range(grid.count())}
    assert pos["fav:0"] == (0, 0) and pos["fav:2"] == (0, 2) and pos["fav:3"] == (1, 0) and pos["fav:4"] == (1, 1)


def test_power_button_marks_on_state(qtbot):
    p, _ = popup(qtbot)
    assert btn(p, f"standby:{BAD}").property("on") is True
    assert btn(p, f"standby:{KUECHE}").property("on") is False
    from deskremote.ui.theme import STYLESHEET as SS
    assert 'QPushButton[on="true"]' in SS


def test_heading_uses_configured_title(qtbot):
    cfg = default_config()
    cfg.title = "Zuhause"
    p = Popup(cfg)
    qtbot.addWidget(p)
    assert p.findChild(QLabel, "heading").text() == "Zuhause"
    cfg.title = "Wohnung"
    p.apply_config()
    assert p.findChild(QLabel, "heading").text() == "Wohnung"


def test_search_button_uses_clean_icon(qtbot):
    p, _ = popup(qtbot)
    b = btn(p, "open_search")
    assert b.text() == "Sendersuche" and not b.icon().isNull()


def test_tooltips_never_contain_raw_markup(qtbot):
    from PySide6.QtWidgets import QWidget
    from deskremote.core.config import Favorite
    cfg = default_config()
    cfg.favorites = [Favorite(EVIL, "http://x")]
    p = Popup(cfg)
    qtbot.addWidget(p)
    p.set_state([RoomState(BAD, EVIL, "uuid:z", "ACTIVE", True, EVIL, 30, False)])
    tips = [w.toolTip() for w in p.findChildren(QWidget) if w.toolTip()]
    assert tips and all("<img" not in t for t in tips)


def test_power_on_is_white_with_ring_not_red():
    import re
    from deskremote.ui.theme import STYLESHEET as SS
    rule = re.search(r'QPushButton\[on="true"\] \{([^}]*)\}', SS).group(1)
    assert "#FFFFFF" in rule and "#E2001A" not in rule


def test_search_link_is_named_sendersuche(qtbot):
    p, _ = popup(qtbot)
    assert btn(p, "open_search").text() == "Sendersuche"


def test_long_favorite_names_are_elided_at_the_end(qtbot):
    from deskremote.core.config import Favorite
    cfg = default_config()
    cfg.favorites = [Favorite("ORF Radio Wien – Das Beste aus Wien", "http://x")]
    p = Popup(cfg)
    qtbot.addWidget(p)
    assert "Das Beste aus Wien" in btn(p, "fav:0").toolTip()
    from deskremote.ui.popup import FavButton
    b = FavButton(cfg.favorites[0].name)
    qtbot.addWidget(b)
    b.resize(90, 30)
    b.show()
    qtbot.waitExposed(b)
    assert b.text().startswith("ORF") and b.text().endswith("…")


def test_group_card_has_one_target_for_whole_group(qtbot):
    p, cfg = popup(qtbot, grouped=True)
    head = p.findChild(QLabel, "target:uuid:zbad")
    assert head is not None and "Badezimmer + Küche" in head.text()
    assert btn(p, f"target:{BAD}") is None and btn(p, f"target:{KUECHE}") is None
    with qtbot.waitSignal(p.target_changed):
        qtbot.mouseClick(head, Qt.LeftButton)
    assert cfg.target_room in (BAD, KUECHE)
    assert head.property("target") is True
    assert p._target_label.text() == "Favoriten · Ziel: Badezimmer + Küche"
    # je Raum weiterhin eigene Lautstärke und eigener Standby
    assert p.findChild(QSlider, f"vol:{KUECHE}") is not None and btn(p, f"standby:{KUECHE}") is not None


def test_target_inside_group_highlights_group(qtbot):
    cfg = default_config()
    cfg.target_room = KUECHE
    p = Popup(cfg)
    qtbot.addWidget(p)
    p.set_online(True)
    p.set_state(rooms(grouped=True))
    assert p.findChild(QLabel, "target:uuid:zbad").property("target") is True



def test_group_header_flows_names_and_station(qtbot):
    from deskremote.ui.theme import RED, WHITE
    p, cfg = popup(qtbot, grouped=True)
    p.show()
    qtbot.waitExposed(p)
    head = p.findChild(QLabel, "target:uuid:zbad")
    member = p.findChild(QLabel, f"member:{BAD}")
    card = head.parentWidget()
    assert head.mapTo(card, head.rect().topLeft()).x() == member.mapTo(card, member.rect().topLeft()).x()
    assert head.wordWrap() is True
    text = head.text()
    assert "Badezimmer + Küche" in text and "·" in text and "FM4" in text
    assert RED not in text  # Ziel ist Wohnzimmer, nicht die Gruppe
    qtbot.mouseClick(head, Qt.LeftButton)
    assert RED in head.text() and WHITE in head.text()


def test_group_header_escapes_everything(qtbot):
    cfg = default_config()
    p = Popup(cfg)
    qtbot.addWidget(p)
    p.set_online(True)
    evil = [RoomState(BAD, EVIL, "uuid:z", "ACTIVE", True, EVIL, 30, False),
            RoomState(KUECHE, "Kueche", "uuid:z", "ACTIVE", True, EVIL, 30, False)]
    p.set_state(evil)
    text = p.findChild(QLabel, "target:uuid:z").text()
    assert "<img" not in text and "&lt;img" in text


def test_version_shown_bottom_right(qtbot):
    import re
    from pathlib import Path
    import deskremote
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1) == deskremote.__version__
    p, _ = popup(qtbot)
    assert p.findChild(QLabel, "version").text() == f"v{deskremote.__version__}"


def test_messages_appear_in_popup_not_as_windows_toast(qtbot, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon
    from deskremote.ui.tray import Tray
    p, cfg = popup(qtbot)
    t = Tray(p, cfg)
    toasts = []
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda self, *a, **k: toasts.append(a))
    t.notify("Sender nicht erreichbar")
    msg = p.findChild(QLabel, "message")
    assert msg.text() == "Sender nicht erreichbar" and not msg.isHidden()
    assert toasts == []


def test_message_clears_itself(qtbot):
    p, _ = popup(qtbot)
    p.show_message("Hallo", timeout_ms=50)
    qtbot.waitUntil(lambda: p.findChild(QLabel, "message").isHidden(), timeout=2000)


def test_favorites_heading_with_target(qtbot):
    p, _ = popup(qtbot)
    assert p._target_label.text() == "Favoriten · Ziel: Wohnzimmer"
    cfg = default_config()
    q = Popup(cfg)
    qtbot.addWidget(q)
    q.set_state(rooms())
    assert q._target_label.text() == "Favoriten · Ziel: Raum anklicken"



def test_popup_grows_upwards_when_anchored_at_bottom(qtbot):
    p, _ = popup(qtbot)
    p.show()
    qtbot.waitExposed(p)
    p.anchor_bottom = p.geometry().bottom()
    bottom = p.geometry().bottom()
    p.resize(p.width(), p.height() + 40)
    assert p.geometry().bottom() == bottom


def test_group_header_has_air_between_lines_and_light_station():
    from deskremote.ui.popup import group_header_html
    html = group_header_html(["Badezimmer", "Küche", "Wohnzimmer"], "All India Radio - Bollywood", True)
    m = re.search(r"line-height:\s*(\d+)%", html)
    assert m and int(m.group(1)) >= 125
    station = re.search(r'<span style="([^"]*)">All India Radio', html).group(1)
    assert "font-weight:400" in station.replace(" ", "")


# --- V2: Album-Bedienung ------------------------------------------------------
def queue_rooms(repeat="off", shuffle=False, grouped=False):
    zb = "uuid:zbad"
    ze = zb if grouped else "uuid:zkueche"
    return [
        RoomState(BAD, "Badezimmer", zb, "ACTIVE", True, "Album 01", 34, False, track="Titel 05",
                  artist="Naked Lunch", is_queue=True, shuffle=shuffle, repeat=repeat),
        RoomState(KUECHE, "Küche", ze, "ACTIVE", grouped, "Album 01" if grouped else None, 31, False,
                  track="Titel 05" if grouped else None, artist="Naked Lunch" if grouped else None,
                  is_queue=grouped, shuffle=shuffle, repeat=repeat),
        RoomState(WOHN, "Wohnzimmer", None, "ACTIVE", False, None, 31, False),
    ]


def qpopup(qtbot, **kw):
    cfg = default_config()
    cfg.target_room = BAD
    p = Popup(cfg)
    qtbot.addWidget(p)
    p.set_online(True)
    p.set_state(queue_rooms(**kw))
    return p


def test_queue_controls_only_for_album_playback(qtbot):
    p = qpopup(qtbot)
    for name in ("prev", "next", "shuffle", "repeat"):
        assert btn(p, f"{name}:uuid:zbad") is not None
        assert btn(p, f"{name}:uuid:zkueche") is None


def test_next_emits_next_track(qtbot):
    p = qpopup(qtbot)
    with qtbot.waitSignal(p.action) as blocker:
        qtbot.mouseClick(btn(p, "next:uuid:zbad"), Qt.LeftButton)
    assert blocker.args == ["next_track", (BAD,)]
    with qtbot.waitSignal(p.action) as blocker:
        qtbot.mouseClick(btn(p, "prev:uuid:zbad"), Qt.LeftButton)
    assert blocker.args == ["previous_track", (BAD,)]


def test_repeat_cycles_off_all_one(qtbot):
    expected = {"off": "all", "all": "one", "one": "off"}
    for now, nxt in expected.items():
        p = qpopup(qtbot, repeat=now)
        with qtbot.waitSignal(p.action) as blocker:
            qtbot.mouseClick(btn(p, "repeat:uuid:zbad"), Qt.LeftButton)
        assert blocker.args == ["set_play_mode", (BAD, False, nxt)]


def test_shuffle_toggles_and_disabled_on_repeat_one(qtbot):
    p = qpopup(qtbot)
    with qtbot.waitSignal(p.action) as blocker:
        qtbot.mouseClick(btn(p, "shuffle:uuid:zbad"), Qt.LeftButton)
    assert blocker.args == ["set_play_mode", (BAD, True, "off")]
    p2 = qpopup(qtbot, repeat="one")
    assert not btn(p2, "shuffle:uuid:zbad").isEnabled()


def test_mode_buttons_show_state(qtbot):
    p = qpopup(qtbot, shuffle=True, repeat="all")
    assert btn(p, "shuffle:uuid:zbad").property("on") is True
    assert btn(p, "repeat:uuid:zbad").property("on") is True
    p2 = qpopup(qtbot)
    assert btn(p2, "shuffle:uuid:zbad").property("on") is False


def test_track_and_artist_shown_instead_of_station(qtbot):
    p = qpopup(qtbot)
    assert shown(head(p, BAD)) == "Badezimmer · Titel 05 · Naked Lunch"


def test_group_header_shows_track_escaped(qtbot):
    p = qpopup(qtbot, grouped=True)
    head = p.findChild(QLabel, "target:uuid:zbad").text()
    assert "Titel 05 · Naked Lunch" in head
    assert btn(p, "next:uuid:zbad") is not None


def test_music_link_in_footer(qtbot):
    p, _ = popup(qtbot)
    with qtbot.waitSignal(p.open_music):
        qtbot.mouseClick(btn(p, "open_music"), Qt.LeftButton)


def test_footer_links_not_clipped(qtbot):
    p, _ = popup(qtbot)
    p.adjustSize()
    p.show()
    qtbot.waitExposed(p)
    for name in ("open_music", "open_search", "open_couple", "open_settings"):
        b = btn(p, name)
        assert b.width() >= b.sizeHint().width(), name


def test_fav_button_shows_logo_when_loaded(qtbot):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QPixmap

    class FakeCovers(QObject):
        loaded = Signal(str, QPixmap)

        def request_logo(self, key, url, size=16):
            pix = QPixmap(16, 16)
            pix.fill(Qt.red)
            return pix

    cfg = default_config()
    cfg.favorites[0].logo = "https://fm4.orf.at/logo.png"
    p = Popup(cfg, covers=FakeCovers())
    qtbot.addWidget(p)
    assert not btn(p, "fav:0").icon().isNull()
    assert btn(p, "fav:1").icon().isNull()


def test_music_favorite_button_uses_cover(qtbot):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QPixmap
    from deskremote.core.config import Favorite
    calls = []

    class FakeCovers(QObject):
        loaded = Signal(str, QPixmap)

        def request_logo(self, key, url, size=16):
            calls.append(("logo", key, url))
            return None

        def request(self, key, url, artist=None, album=None, size=48, server_host=None, strict=False):
            calls.append(("cover", key, url, server_host, size))
            pix = QPixmap(16, 16)
            pix.fill(Qt.red)
            return pix

    cfg = default_config()
    cover = "http://192.168.0.176:47366/?albumArtist=Die%20Sterne&album=Moon"
    cfg.favorites = [Favorite("Album 22 · Die Sterne", "dlna-playcontainer://x?cid=c&fii=0", kind="album",
                              meta="<DIDL-Lite/>", cover=cover)]
    p = Popup(cfg, covers=FakeCovers())
    qtbot.addWidget(p)
    assert calls == [("cover", "fav:dlna-playcontainer://x?cid=c&fii=0", cover, None, 16)]
    assert not btn(p, "fav:0").icon().isNull()



def test_music_cover_only_from_home_network(qtbot):
    """Fix 1: Cover-Host darf nicht aus der Cover-Adresse selbst stammen."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QPixmap
    from deskremote.core.config import Favorite
    calls = []

    class FakeCovers(QObject):
        loaded = Signal(str, QPixmap)

        def request(self, key, url, artist=None, album=None, size=48, server_host=None, strict=False):
            calls.append(server_host)
            return None

    cfg = default_config()
    cfg.favorites = [Favorite("A", "dlna-playcontainer://x?fii=0", kind="album", cover="http://127.0.0.1:8080/x")]
    p = Popup(cfg, covers=FakeCovers())
    qtbot.addWidget(p)
    assert calls == [None]


def test_ampersand_in_favorite_name_shown_literally(qtbot):
    from deskremote.core.config import Favorite
    cfg = default_config()
    cfg.favorites = [Favorite("Belle & Sebastian · Zufall", "dlna-playcontainer://x?fii=0", kind="artist")]
    p = Popup(cfg)
    qtbot.addWidget(p)
    b = btn(p, "fav:0")
    b.resize(600, 30)
    assert b.text() == "Belle && Sebastian · Zufall"


def test_ampersand_in_tray_menu_shown_literally(qtbot):
    from deskremote.core.config import Favorite
    from deskremote.ui.tray import Tray
    cfg = default_config()
    cfg.favorites = [Favorite("Belle & Sebastian", "https://x")]
    t = Tray(Popup(cfg), cfg)
    t._build_menu()
    assert t._menu.actions()[0].text() == "▶ Belle && Sebastian"



def spotify_group(track="Titel 10", artist="Whitney"):
    z = "uuid:zspot"
    return [
        RoomState(BAD, "Badezimmer", z, "ACTIVE", True, None, 34, True, track=track, artist=artist),
        RoomState(KUECHE, "Küche", z, "ACTIVE", True, None, 31, True, track=track, artist=artist),
        RoomState(WOHN, "Wohnzimmer", None, "ACTIVE", False, None, 31, False),
    ]


def test_spotify_group_header_shows_icon_and_track(qtbot):
    p, _ = popup(qtbot)
    p.set_state(spotify_group())
    head = p.findChild(QLabel, "target:uuid:zspot")
    assert "data:image/png" in head.text()
    assert "Titel 10 · Whitney" in head.text()
    assert "Spotify" not in head.text().split("base64,")[0]


def test_spotify_icon_survives_target_change(qtbot):
    p, _ = popup(qtbot)
    p.set_state(spotify_group())
    p._set_target(BAD)
    assert "data:image/png" in p.findChild(QLabel, "target:uuid:zspot").text()


def test_spotify_track_text_is_escaped(qtbot):
    p, _ = popup(qtbot)
    p.set_state(spotify_group(track="<b>Laut</b> & leise", artist="A<i>"))
    text = p.findChild(QLabel, "target:uuid:zspot").text()
    assert "&lt;b&gt;Laut&lt;/b&gt; &amp; leise" in text and "<b>" not in text and "<i>" not in text


def test_no_spotify_icon_without_spotify(qtbot):
    p, _ = popup(qtbot, grouped=True)
    assert "data:image" not in p.findChild(QLabel, "target:uuid:zbad").text()


def test_spotify_without_track_shows_word(qtbot):
    p, _ = popup(qtbot)
    p.set_state(spotify_group(track=None, artist=None))
    assert p.findChild(QLabel, "target:uuid:zspot").text().endswith("Spotify</span></p>")


def test_spotify_icon_is_white_on_transparent(qtbot):
    from deskremote.ui.theme import spotify_icon_pixmap
    img = spotify_icon_pixmap(13).toImage()
    colors = {(c.red(), c.green(), c.blue(), c.alpha())
              for c in (img.pixelColor(x, y) for x in range(img.width()) for y in range(img.height()))}
    assert (255, 255, 255, 255) in colors
    assert any(a == 0 for *_, a in colors)
    assert all(r == g == b for r, g, b, a in colors if a == 255)  # nur Weiß, kein Grün



def test_single_room_header_wraps_like_group(qtbot):
    """Abnahme v2.2: Einzelraum wie Gruppe – „Raum · Titel“, Titel weiß und umlaufend statt abgeschnitten."""
    p, _ = popup(qtbot)
    long = "Kreisky - ein sehr langer Beispieltitel (Live-Session, erweiterte Fassung) | Titel 12 am Morgen"
    p.set_state([RoomState(BAD, "Badezimmer", "uuid:zbad", "ACTIVE", True, long, 34, False)])
    p.show()
    p.resize(400, p.height())
    qtbot.wait(50)
    h = head(p, BAD)
    assert h.wordWrap() is True
    assert shown(h) == f"Badezimmer · {long}"
    assert h.height() >= h.heightForWidth(h.width()) > 2 * h.fontMetrics().height()
    assert '#FFFFFF; font-weight:400">Kreisky' in h.text()  # weiß und normal wie in der Gruppe
