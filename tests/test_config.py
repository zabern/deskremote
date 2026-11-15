import json

import pytest

from deskremote.core.config import Favorite, default_config, load_config, needs_logo_lookup, save_config


def test_default_favorites():
    cfg = default_config()
    assert [f.name for f in cfg.favorites] == ["FM4", "Ö1", "Kronehit"]
    assert [f.url for f in cfg.favorites] == [
        "https://orf-live.ors-shoutcast.at/fm4-q2a",
        "http://orf-live.ors-shoutcast.at/oe1-q2a",
        "http://onair-ha1.krone.at/kronehit1058.mp3",
    ]


def test_default_hotkeys():
    cfg = default_config()
    assert cfg.hotkeys["volume_up"] == "Ctrl+Alt+Up"
    assert cfg.hotkeys["volume_down"] == "Ctrl+Alt+Down"
    assert cfg.hotkeys["play_pause"] == "Ctrl+Alt+Space"
    assert cfg.hotkeys["favorite_3"] == "Ctrl+Alt+3"
    assert cfg.hotkeys["standby"] == "Ctrl+Alt+0"
    assert cfg.search_country == "AT"
    assert cfg.volume_step == 5


def test_roundtrip(tmp_path):
    path = tmp_path / "sub" / "config.json"
    cfg = default_config()
    cfg.target_room = "uuid:x"
    cfg.last_host = "http://192.168.0.176:47365"
    cfg.favorites.append(Favorite("Ö3", "https://orf-live.ors-shoutcast.at/oe3-q2a"))
    save_config(cfg, path)
    assert "Ö1" in path.read_text(encoding="utf-8")
    assert load_config(path) == cfg


def test_corrupt_file_gives_default(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{kaputt", encoding="utf-8")
    assert load_config(path) == default_config()


def test_missing_file_gives_default(tmp_path):
    assert load_config(tmp_path / "fehlt.json") == default_config()


def test_save_is_atomic(tmp_path, monkeypatch):
    import os
    import pytest
    path = tmp_path / "config.json"
    save_config(default_config(), path)
    before = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("Stromausfall")

    monkeypatch.setattr(os, "replace", boom)
    cfg = default_config()
    cfg.favorites = []
    with pytest.raises(OSError):
        save_config(cfg, path)
    assert path.read_text(encoding="utf-8") == before


def test_wrong_types_give_default(tmp_path):
    path = tmp_path / "config.json"
    for bad in ('{"hotkeys": {"standby": 5}}', '{"favorites": [{"name": 3, "url": "http://x"}]}',
                '{"favorites": [{"name": "X", "url": "javascript:alert(1)"}]}', '{"volume_step": "laut"}', '[]'):
        path.write_text(bad, encoding="utf-8")
        cfg = load_config(path)
        assert cfg.favorites == default_config().favorites, bad
        assert all(isinstance(v, str) for v in cfg.hotkeys.values()), bad


def test_title_default_and_roundtrip(tmp_path):
    cfg = default_config()
    assert cfg.title == "DeskRemote"
    cfg.title = "Zuhause"
    save_config(cfg, tmp_path / "c.json")
    assert load_config(tmp_path / "c.json").title == "Zuhause"


def test_online_covers_default_and_roundtrip(tmp_path):
    cfg = default_config()
    assert cfg.online_covers is True
    cfg.online_covers = False
    save_config(cfg, tmp_path / "c.json")
    assert load_config(tmp_path / "c.json").online_covers is False


def test_config_roundtrip_logo(tmp_path):
    cfg = default_config()
    cfg.favorites[0].logo = "https://fm4.orf.at/logo.png"
    cfg.favorites[1].logo = ""  # gesucht, nichts gefunden
    save_config(cfg, tmp_path / "c.json")
    back = load_config(tmp_path / "c.json")
    assert back.favorites[0].logo == "https://fm4.orf.at/logo.png" and back.favorites[1].logo == ""
    assert back.favorites[2].logo is None


PC = "dlna-playcontainer://uuid%3Ax?sid=s&cid=c&md=0&fii=0"


def _write_favs(path, favs):
    path.write_text(json.dumps({"favorites": favs}), encoding="utf-8")


def test_old_favorite_without_kind_is_stream(tmp_path):
    path = tmp_path / "c.json"
    _write_favs(path, [{"name": "FM4", "url": "https://x/fm4"}])
    fav = load_config(path).favorites[0]
    assert fav.kind == "stream" and not fav.is_music


def test_music_favorite_roundtrip(tmp_path):
    cfg = default_config()
    fav = Favorite("Album 22 · Die Sterne", PC, kind="album", meta="<DIDL-Lite/>",
                   cover="http://192.168.0.176:47366/?x", count=10)
    cfg.favorites.append(fav)
    save_config(cfg, tmp_path / "c.json")
    back = load_config(tmp_path / "c.json").favorites[-1]
    assert back == fav and back.is_music


@pytest.mark.parametrize("bad", [
    {"kind": "album", "url": "http://x/stream"},
    {"kind": "stream", "url": "dlna-playcontainer://x"},
    {"kind": "album", "url": "dlna-playcontainer://x", "meta": "a" * 16_001},
    {"kind": "lied", "url": "dlna-playcontainer://x"},
    {"kind": "artist", "url": "dlna-playcontainer://x", "count": "10"},
    {"kind": "artist", "url": "dlna-playcontainer://x", "count": True},
    {"kind": "artist", "url": "dlna-playcontainer://x", "count": -1},
    {"kind": "album", "url": "dlna-playcontainer://x", "meta": 5},
])
def test_invalid_music_favorite_falls_back_to_defaults(tmp_path, bad):
    path = tmp_path / "c.json"
    _write_favs(path, [dict({"name": "X"}, **bad)])
    assert load_config(path).favorites == default_config().favorites


def test_music_cover_must_be_http(tmp_path):
    path = tmp_path / "c.json"
    _write_favs(path, [{"name": "A", "url": PC, "kind": "album", "cover": "file:///c"}])
    fav = load_config(path).favorites[0]
    assert fav.cover is None and fav.kind == "album"


def test_logo_lookup_skips_music_favorites():
    assert needs_logo_lookup(Favorite("FM4", "https://x"))
    assert not needs_logo_lookup(Favorite("A", PC, kind="album"))
    assert not needs_logo_lookup(Favorite("FM4", "https://x", logo=""))



@pytest.mark.parametrize("field", ["cover", "logo"])
def test_malformed_image_url_is_dropped_not_crashing(tmp_path, field):
    path = tmp_path / "c.json"
    _write_favs(path, [{"name": "A", "url": PC, "kind": "album", field: "http://[::1"}])
    fav = load_config(path).favorites[0]
    assert getattr(fav, field) is None


def test_absurd_artist_count_rejected(tmp_path):
    path = tmp_path / "c.json"
    _write_favs(path, [{"name": "A", "url": PC, "kind": "artist", "count": 10**12}])
    assert load_config(path).favorites == default_config().favorites



def test_old_deskremote_config_is_taken_over(tmp_path, monkeypatch):
    """Umbenennung in DeskRemote: vorhandene Einstellungen aus %APPDATA%/RaumfeldDesk übernehmen."""
    from deskremote.core.config import config_path
    monkeypatch.setenv("APPDATA", str(tmp_path))
    old = tmp_path / "RaumfeldDesk" / "config.json"
    old.parent.mkdir()
    cfg = default_config()
    cfg.title = "Zuhause"
    save_config(cfg, old)
    loaded = load_config()
    assert loaded.title == "Zuhause"
    assert config_path() == tmp_path / "DeskRemote" / "config.json" and config_path().exists()
    assert old.exists()  # alte Datei bleibt als Sicherung


def test_new_config_wins_over_old(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for folder, title in (("RaumfeldDesk", "Alt"), ("DeskRemote", "Neu")):
        (tmp_path / folder).mkdir()
        cfg = default_config()
        cfg.title = title
        save_config(cfg, tmp_path / folder / "config.json")
    assert load_config().title == "Neu"
