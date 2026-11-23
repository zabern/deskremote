"""Gemeinsame Favoritenliste mit der Teufel-App: Config, Anlage, Worker und Oberfläche."""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QListWidget, QPushButton

from deskremote.core.anlage import Anlage, PlayError, anlage_favorite
from deskremote.core.config import (
    ANLAGE, Favorite, default_config, load_config, save_config, take_anlage_favorites,
)
from deskremote.raumfeld.favorites import parse_favorites
from deskremote.raumfeld.models import Device, Room, Zone, ZoneConfig
from deskremote.raumfeld.upnp import UpnpError
from deskremote.ui.hotkeys import dispatch
from deskremote.ui.search import SearchWindow
from deskremote.ui.settings import ANLAGE_LABELS, NO_MANUAL, NOT_ON_ANLAGE, SettingsWindow

FIX = Path(__file__).parent / "fixtures"
FAV_XML = (FIX / "favorites_my.xml").read_text(encoding="utf-8")
MS_UDN = "uuid:c24a80f6-f43a-51a1-9031-97cedeb3c9f3"
FAVS = [f for af in parse_favorites(FAV_XML) if (f := anlage_favorite(MS_UDN, af))]
FM4, OE1, PARADISE, ALBUM, ARTIST = FAVS


def anlage_cfg():
    cfg = default_config()
    take_anlage_favorites(cfg, list(FAVS))
    return cfg


# --- Umwandlung -------------------------------------------------------------
def test_anlage_favorite_fields():
    assert FM4.kind == ANLAGE and FM4.sub == "station" and FM4.on_anlage
    assert FM4.ref == "0/Favorites/MyFavorites/315" and FM4.target == "0/RadioTime/Search/s-s8235"
    assert FM4.url.startswith("dlna-playsingle://") and FM4.logo.startswith("https://cdn-profiles.tunein.com/")
    assert not FM4.is_music and FM4.key == FM4.target
    assert ALBUM.is_music and ALBUM.cover and ALBUM.logo == "" and ALBUM.url.startswith("dlna-playcontainer://")


# --- Config ---------------------------------------------------------------
def test_new_keys_roundtrip_and_old_list_untouched(tmp_path):
    path = tmp_path / "config.json"
    cfg = anlage_cfg()
    cfg.hotkey_favorites = [OE1.key, "", ""]
    save_config(cfg, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert all(f["kind"] == "stream" for f in data["favorites"])  # ältere Versionen lesen das weiter
    assert len(data["anlage_favorites"]) == 5
    assert load_config(path) == cfg


def test_old_config_without_new_keys_still_loads(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"favorites": [{"name": "FM4", "url": "https://x/fm4"}], "target_room": "uuid:bad"}),
                    encoding="utf-8")
    cfg = load_config(path)
    assert cfg.target_room == "uuid:bad" and cfg.anlage_favorites is None
    assert cfg.active_favorites() == cfg.favorites and cfg.hotkey_favorites == ["", "", ""]


def test_bad_cached_entry_is_dropped_not_the_whole_config(tmp_path):
    path = tmp_path / "config.json"
    good = {"name": "FM4", "url": FM4.url, "kind": "anlage", "ref": FM4.ref, "target": FM4.target, "sub": "station"}
    bad = [dict(good, ref="0/My Music/Albums/x"), dict(good, url="http://evil"), dict(good, target="0/Favorites/x"),
           dict(good, sub="virus"), dict(good, logo="file:///etc/passwd"), "kaputt", dict(good, name=3)]
    path.write_text(json.dumps({"target_room": "uuid:bad", "anlage_favorites": [good] + bad,
                                "hotkey_favorites": [5, "x"]}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.target_room == "uuid:bad"
    assert [f.target for f in cfg.anlage_favorites] == [FM4.target, FM4.target]  # logo file:// → ohne Logo
    assert cfg.anlage_favorites[1].logo == ""
    assert cfg.hotkey_favorites == ["", "x", ""]


def test_active_list_switches_after_first_load():
    cfg = default_config()
    assert [f.name for f in cfg.active_favorites()] == ["FM4", "Ö1", "Kronehit"]
    take_anlage_favorites(cfg, FAVS)
    assert cfg.active_favorites() == FAVS
    take_anlage_favorites(cfg, [])
    assert cfg.active_favorites() == []  # leere Teufel-Liste bleibt leer, kein Rückfall


def test_first_load_seeds_hotkeys_from_old_list():
    cfg = default_config()  # FM4, Ö1, Kronehit
    changed, unmatched = take_anlage_favorites(cfg, FAVS)
    assert changed
    # „FM4“ ⊂ „ORF Radio FM4“; Ö1 und Kronehit stehen nicht in der Test-Liste → alte Adresse bleibt
    assert cfg.hotkey_favorites == [FM4.target, cfg.favorites[1].url, cfg.favorites[2].url]
    assert unmatched == ["Ö1", "Kronehit"]
    cfg.target_room = "uuid:bad"
    assert dispatch("favorite_2", cfg) is None  # nie still auf einen anderen Sender springen
    changed, unmatched = take_anlage_favorites(cfg, FAVS)
    assert not changed and unmatched == []  # nur beim ersten Mal


def test_seeding_needs_unique_match():
    cfg = default_config()
    cfg.favorites = [Favorite("Radio", "http://x")]  # passt auf „Radio Paradise“ und „ORF Radio …“
    take_anlage_favorites(cfg, FAVS)
    assert cfg.hotkey_favorites == ["http://x", "", ""]  # nicht eindeutig → nichts Falsches belegt


def test_hotkey_pin_and_position_fallback():
    cfg = anlage_cfg()
    cfg.hotkey_favorites = ["", PARADISE.key, "0/RadioTime/Search/weg"]
    cfg.target_room = "uuid:bad"
    assert dispatch("favorite_1", cfg) == ("play_favorite", ("uuid:bad", FM4))  # Platz 1
    assert dispatch("favorite_2", cfg) == ("play_favorite", ("uuid:bad", PARADISE))  # festgelegt
    assert dispatch("favorite_3", cfg) is None  # festgelegt, aber nicht mehr da → nichts Falsches spielen


# --- Anlage -----------------------------------------------------------------
class FakeMSDevice:
    def __init__(self, log):
        self.log = log
        self.entries = parse_favorites(FAV_XML)

    def call(self, service, action, **args):
        self.log.append(("ms", action, args))
        if action == "Browse" and args["BrowseFlag"] == "BrowseMetadata":
            return {"Result": f"<DIDL-Lite id='{args['ObjectID']}'/>"}
        if action == "Browse":
            return {"Result": FAV_XML, "NumberReturned": "5", "TotalMatches": "5"}
        if action == "CreateReference":
            return {"NewID": "0/Favorites/MyFavorites/2000"}
        if action == "Search":
            return {"Result": FAV_XML.replace("0/Favorites/MyFavorites/315", "0/RadioTime/Search/s-s8235")}
        return {}


class FakeRenderer:
    def __init__(self, log):
        self.log = log

    def call(self, service, action, **args):
        self.log.append(("zone", action, args))
        if action == "GetTransportInfo":
            return {"CurrentTransportState": "PLAYING"}
        return {}


class FakeHost:
    base_url = "http://h:47365"

    def __init__(self, with_server=True):
        self.with_server = with_server

    def get_zones(self, update_id=None):
        return ZoneConfig([Zone("uuid:zbad", [Room("uuid:bad", "Badezimmer", "ACTIVE", ["uuid:spk"])])], []), "1"

    def list_devices(self):
        devs = {"uuid:zbad": Device("uuid:zbad", "urn:schemas-upnp-org:device:MediaRenderer:1", "http://h/zone.xml", "z")}
        if self.with_server:
            devs[MS_UDN] = Device(MS_UDN, "urn:schemas-upnp-org:device:MediaServer:1", "http://h/ms.xml", "MS")
        return devs


def make_anlage(with_server=True):
    log = []
    ms, zone = FakeMSDevice(log), FakeRenderer(log)
    a = Anlage(FakeHost(with_server), lambda loc: ms if loc.endswith("ms.xml") else zone, sleep=lambda s: None)
    return a, log


def test_anlage_lists_favorites():
    a, log = make_anlage()
    assert a.favorites() == FAVS


def test_anlage_without_media_server():
    a, _ = make_anlage(with_server=False)
    with pytest.raises(UpnpError):
        a.favorites()


def test_anlage_add_and_remove_resolve_fresh_entry():
    a, log = make_anlage()
    a.add_favorite("0/RadioTime/Search/s-s16660")
    assert ("ms", "CreateReference", {"ContainerID": "0/Favorites/MyFavorites",
                                      "ObjectID": "0/RadioTime/Search/s-s16660"}) in log
    a.remove_favorite(FM4.target)
    assert ("ms", "DestroyObject", {"ObjectID": "0/Favorites/MyFavorites/315"}) in log
    log.clear()
    a.remove_favorite("0/RadioTime/Search/gibtsnicht")
    assert not any(act == "DestroyObject" for _, act, _ in log)


def test_play_teufel_favorite_like_the_app():
    a, log = make_anlage()
    a.play_favorite("uuid:bad", FM4)
    set_args = next(args for who, act, args in log if who == "zone" and act == "SetAVTransportURI")
    assert set_args["CurrentURI"].endswith("&iid=0%2FFavorites%2FMyFavorites%2F315")
    assert "0/Favorites/MyFavorites/315" in set_args["CurrentURIMetaData"]  # Metadaten frisch von der Anlage
    assert not any(act == "SetPlayMode" for _, act, _ in log)


def test_play_artist_favorite_turns_on_shuffle():
    a, log = make_anlage()
    a.play_favorite("uuid:bad", ARTIST)
    assert ("zone", "SetPlayMode", {"InstanceID": 0, "NewPlayMode": "SHUFFLE"}) in log


def test_play_removed_favorite_says_so():
    a, _ = make_anlage()
    gone = Favorite("Weg", FM4.url, "", ANLAGE, "", None, 0, "0/Favorites/MyFavorites/9", "0/RadioTime/x", "station")
    with pytest.raises(PlayError, match="nicht mehr"):
        a.play_favorite("uuid:bad", gone)


def test_play_search_hit_plays_object_itself():
    a, log = make_anlage()
    hit = a.search_stations("FM4")[0]
    from deskremote.core.anlage import station_favorite
    a.play_favorite("uuid:bad", station_favorite(MS_UDN, hit))
    set_args = next(args for who, act, args in log if act == "SetAVTransportURI")
    assert set_args["CurrentURI"].endswith("&iid=0%2FRadioTime%2FSearch%2Fs-s8235")


# --- Sendersuche ----------------------------------------------------------
def search_window(qtbot, cfg, hits):
    w = SearchWindow(lambda text: hits, cfg, run_async=False)
    qtbot.addWidget(w)
    return w


HIT = Favorite("ORF Radio Oberösterreich", "dlna-playsingle://x", "", ANLAGE, "", None, 0, "",
               "0/RadioTime/Search/s-s16660", "station")


def test_search_star_requests_anlage_and_fills_on_confirmation(qtbot):
    cfg = anlage_cfg()
    w = search_window(qtbot, cfg, [HIT, Favorite(FM4.name, FM4.url, kind=ANLAGE, target=FM4.target, sub="station")])
    w.search_now("ober")
    star = w.findChild(QPushButton, "star:0")
    assert star.text() == "☆" and w.findChild(QPushButton, "star:1").text() == "★"
    with qtbot.waitSignal(w.add_requested) as blocker:
        qtbot.mouseClick(star, Qt.LeftButton)
    assert blocker.args == [HIT.target] and star.text() == "…"
    assert cfg.anlage_favorites == FAVS  # nichts lokal angelegt – die Anlage entscheidet
    take_anlage_favorites(cfg, FAVS + [Favorite(HIT.name, HIT.url, kind=ANLAGE, ref="0/Favorites/MyFavorites/7",
                                                target=HIT.target, sub="station")])
    w.refresh_stars()
    assert star.text() == "★"


def test_search_play_emits_hit(qtbot):
    w = search_window(qtbot, anlage_cfg(), [HIT])
    w.search_now("ober")
    with qtbot.waitSignal(w.play) as blocker:
        qtbot.mouseClick(w.findChild(QPushButton, "play:0"), Qt.LeftButton)
    assert blocker.args == [HIT]


def test_search_offline_message(qtbot):
    def broken(text):
        raise RuntimeError("offline")
    w = SearchWindow(broken, anlage_cfg(), run_async=False)
    qtbot.addWidget(w)
    w.search_now("x")
    assert "Anlage erreichbar" in w.status.text()


# --- Einstellungen --------------------------------------------------------
def test_settings_shows_teufel_list_and_locks_sort_rename(qtbot):
    cfg = anlage_cfg()
    w = SettingsWindow(cfg, save=lambda c: None)
    qtbot.addWidget(w)
    lst = w.findChild(QListWidget, "favorites")
    assert lst.count() == 5 and "Teufel-Favorit · Sender" in lst.item(0).text()
    for name in ("up", "down", "rename", "manual"):
        assert not w.findChild(QPushButton, name).isEnabled()
    assert w.findChild(QPushButton, "delete").isEnabled()


def test_settings_delete_asks_and_removes_on_anlage(qtbot, monkeypatch):
    cfg = anlage_cfg()
    saved, removed, asked = [], [], []
    w = SettingsWindow(cfg, save=saved.append)
    qtbot.addWidget(w)
    w.remove_on_anlage.connect(removed.append)
    monkeypatch.setattr(w, "confirm_removal", lambda names: asked.append(names) or True)
    w.findChild(QListWidget, "favorites").setCurrentRow(1)
    qtbot.mouseClick(w.findChild(QPushButton, "delete"), Qt.LeftButton)
    qtbot.mouseClick(w.findChild(QPushButton, "save"), Qt.LeftButton)
    assert asked == [[OE1.name]] and removed == [[OE1.target]]
    assert OE1 not in cfg.anlage_favorites and saved


def test_settings_delete_declined_changes_nothing(qtbot, monkeypatch):
    cfg = anlage_cfg()
    saved, removed = [], []
    w = SettingsWindow(cfg, save=saved.append)
    qtbot.addWidget(w)
    w.remove_on_anlage.connect(removed.append)
    monkeypatch.setattr(w, "confirm_removal", lambda names: False)
    w.findChild(QListWidget, "favorites").setCurrentRow(0)
    qtbot.mouseClick(w.findChild(QPushButton, "delete"), Qt.LeftButton)
    qtbot.mouseClick(w.findChild(QPushButton, "save"), Qt.LeftButton)
    assert removed == [] and saved == [] and cfg.anlage_favorites == FAVS


def test_settings_pins_hotkeys(qtbot):
    cfg = anlage_cfg()
    w = SettingsWindow(cfg, save=lambda c: None)
    qtbot.addWidget(w)
    old_oe1 = cfg.favorites[1].url
    combo = w.findChild(QComboBox, "pin:2")
    assert combo.currentData() == old_oe1 and combo.currentText() == "(nicht mehr in der Liste)"
    assert combo.count() == 7  # Platz 2 + 5 Favoriten + „nicht mehr da“
    combo.setCurrentIndex(combo.findData(PARADISE.key))
    qtbot.mouseClick(w.findChild(QPushButton, "save"), Qt.LeftButton)
    assert cfg.hotkey_favorites[:2] == [FM4.key, PARADISE.key]  # Platz 1 aus FM4 vorbelegt


def test_settings_keeps_pending_delete_when_teufel_list_changes(qtbot):
    cfg = anlage_cfg()
    w = SettingsWindow(cfg, save=lambda c: None)
    qtbot.addWidget(w)
    w.findChild(QListWidget, "favorites").setCurrentRow(0)
    qtbot.mouseClick(w.findChild(QPushButton, "delete"), Qt.LeftButton)
    take_anlage_favorites(cfg, FAVS + [HIT])  # jemand legt in der Teufel-App etwas an
    w.sync_new_favorites()
    names = [w.list.item(i).text() for i in range(w.list.count())]
    assert len(names) == 5 and not any(n.startswith(FM4.name) for n in names) and any(HIT.name in n for n in names)


def test_new_texts_are_translated():
    from deskremote.i18n import EN
    assert all(t in EN for t in list(ANLAGE_LABELS.values()) + [NOT_ON_ANLAGE, NO_MANUAL])


# --- Musik-Sterne -----------------------------------------------------------
def test_music_star_adds_album_on_anlage(qtbot):
    from test_music import FIRST_ALBUM, FakeMS
    from deskremote.ui.music import MusicWindow
    cfg = anlage_cfg()
    cfg.target_room = "uuid:bad"
    w = MusicWindow([FakeMS()], None, cfg, save=lambda c: None, run_async=False)
    qtbot.addWidget(w)
    w.show_albums()
    star = w.findChild(QPushButton, "star:0")
    assert star.text() == "☆"
    with qtbot.waitSignal(w.add_requested) as blocker:
        qtbot.mouseClick(star, Qt.LeftButton)
    assert blocker.args == [FIRST_ALBUM] and star.text() == "…"
    # gleiches Album unter dem Künstler-Pfad steht schon in den Teufel-Favoriten
    same = Favorite("Album 01", ALBUM.url, kind=ANLAGE, ref="0/Favorites/MyFavorites/8",
                    target="0/My Music/Artists/Naked%20Lunch/Naked Lunch+Album%2001", sub="album")
    take_anlage_favorites(cfg, FAVS + [same])
    w.refresh_stars()
    assert star.text() == "★"


# --- Worker -----------------------------------------------------------------
def test_worker_loads_favorites_after_connect_and_after_add(qtbot):
    from test_worker import FakeAnlage, FakeHost as WorkerHost
    from deskremote.ui.worker import Worker

    class WithFavs(FakeAnlage):
        fail_load = False

        def favorites(self):
            if self.fail_load:
                raise UpnpError("TuneIn 503")
            return FAVS

        def add_favorite(self, object_id):
            self._rec("add_favorite", object_id)
            return FAVS + [HIT]

    anlage = WithFavs(WorkerHost())
    w = Worker(default_config(), make_anlage=lambda url: anlage, discover=lambda last: "http://h:47365",
               save=lambda c: None, poll_interval=0.2, retry_interval=0.1)
    try:
        with qtbot.waitSignal(w.favorites_loaded, timeout=3000) as first:
            w.start()
        assert first.args[0] == FAVS
        errors = []
        w.error.connect(errors.append)
        with qtbot.waitSignal(w.favorites_loaded, timeout=3000) as added:
            w.submit("add_favorite", HIT.target)
        assert added.args[0][-1] == HIT and ("add_favorite", HIT.target) in anlage.calls
        anlage.fail_load = True
        w.submit("load_favorites")
        qtbot.wait(400)
        assert errors == []  # Lesen im Hintergrund bleibt stumm
    finally:
        w.stop()


def test_worker_offline_add_and_remove_are_reported(qtbot):
    from deskremote.ui.worker import Worker
    w = Worker(default_config(), make_anlage=lambda url: None, discover=lambda last: None,
               save=lambda c: None, poll_interval=0.2, retry_interval=0.1)
    errors = []
    w.error.connect(errors.append)
    try:
        w.start()
        w.submit("load_favorites")
        w.submit("remove_favorite", FM4.target)
        qtbot.waitUntil(lambda: bool(errors), timeout=3000)
        qtbot.wait(300)
        assert errors == ["Anlage nicht erreichbar"]  # Löschen gemeldet, stilles Laden nicht
    finally:
        w.stop()


def test_removal_question_is_plain_text(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    seen = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: seen.append(self.textFormat()) or 0)
    w = SettingsWindow(anlage_cfg(), save=lambda c: None)
    qtbot.addWidget(w)
    assert w.confirm_removal(["<img src='file://x/a.png'>"]) is False
    assert seen == [Qt.PlainText]
