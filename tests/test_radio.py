from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from deskremote.core.config import Favorite
from deskremote.core.radio import RadioBrowser, SearchError, Station

FIX = (Path(__file__).parent / "fixtures" / "radiobrowser_fm4.json").read_text(encoding="utf-8")


def rb(handler, servers=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return RadioBrowser(client, servers=servers) if servers else RadioBrowser(client)


def test_search_parses_fixture():
    res = rb(lambda r: httpx.Response(200, text=FIX)).search("FM4")
    assert res and isinstance(res[0], Station)
    assert res[0].name.startswith("FM4")
    assert res[0].url.startswith("https://orf-live.ors-shoutcast.at/fm4")
    assert res[0].country == "AT" and res[0].codec == "MP3" and res[0].bitrate > 0


def test_search_query_params():
    seen = {}

    def h(req):
        seen["q"] = parse_qs(req.url.query.decode())
        seen["ua"] = req.headers["User-Agent"]
        seen["path"] = req.url.path
        return httpx.Response(200, text="[]")

    rb(h).search("kronehit")
    q = seen["q"]
    assert seen["path"] == "/json/stations/search"
    assert q["name"] == ["kronehit"] and q["countrycode"] == ["AT"]
    assert q["hidebroken"] == ["true"] and q["order"] == ["votes"] and q["reverse"] == ["true"]
    assert q["limit"] == ["30"]
    assert seen["ua"] == "DeskRemote/0.1"


def test_search_without_country():
    seen = {}

    def h(req):
        seen["q"] = parse_qs(req.url.query.decode())
        return httpx.Response(200, text="[]")

    rb(h).search("fm4", country=None)
    assert "countrycode" not in seen["q"]


def test_search_tries_next_server_then_raises():
    hosts = []

    def h(req):
        hosts.append(req.url.host)
        if req.url.host == "a":
            raise httpx.ConnectError("weg")
        return httpx.Response(200, text=FIX)

    assert rb(h, servers=["a", "b"]).search("FM4")
    assert hosts == ["a", "b"]

    def dead(req):
        raise httpx.ConnectError("weg")

    with pytest.raises(SearchError, match="Senderverzeichnis nicht erreichbar"):
        rb(dead, servers=["a", "b"]).search("FM4")


def test_entries_without_url_are_skipped():
    data = '[{"stationuuid":"1","name":"X","url_resolved":"","codec":"MP3","bitrate":0,"votes":1,"countrycode":"AT"}]'
    assert rb(lambda r: httpx.Response(200, text=data)).search("x") == []


def test_to_favorite():
    st = Station("u", "FM4 | ORF | HQ", "https://x", "MP3", 192, 5, "AT")
    assert RadioBrowser.to_favorite(st) == Favorite("FM4 | ORF | HQ", "https://x", "")


def test_search_has_overall_deadline_per_server():
    import time

    def h(req):
        if req.url.host == "a":
            time.sleep(1.5)  # z. B. hängende Namensauflösung
        return httpx.Response(200, text=FIX)

    radio = RadioBrowser(httpx.Client(transport=httpx.MockTransport(h)), servers=["a", "b"], deadline=0.3)
    t = time.monotonic()
    assert radio.search("FM4")
    assert time.monotonic() - t < 1.2


def test_odd_values_are_tolerated():
    data = '[{"stationuuid":"1","name":"X","url_resolved":"http://x","codec":"MP3","bitrate":"n/a","votes":null,"countrycode":"AT"}]'
    res = rb(lambda r: httpx.Response(200, text=data)).search("x")
    assert res[0].bitrate == 0 and res[0].votes == 0


def test_station_names_are_cleaned_and_capped():
    import json
    data = json.dumps([{"stationuuid": "1", "name": "A\x00B\u202e" + "x" * 500, "url_resolved": "http://x",
                        "codec": "MP3", "bitrate": 1, "votes": 1, "countrycode": "AT"}])
    st = rb(lambda r: httpx.Response(200, text=data)).search("x")[0]
    assert st.name.startswith("AB") and "\x00" not in st.name and "\u202e" not in st.name
    assert len(st.name) <= 120


def test_station_favicon_parsed():
    import json
    data = json.dumps([
        {"stationuuid": "1", "name": "A", "url_resolved": "http://a", "favicon": "https://a.example/logo.png"},
        {"stationuuid": "2", "name": "B", "url_resolved": "http://b", "favicon": "javascript:alert(1)"},
        {"stationuuid": "3", "name": "C", "url_resolved": "http://c", "favicon": ""},
    ])
    res = rb(lambda r: httpx.Response(200, text=data)).search("x")
    assert [s.favicon for s in res] == ["https://a.example/logo.png", None, None]


def test_logo_lookup_by_url_for_old_favorites():
    from urllib.parse import parse_qs
    seen = {}

    def h(req):
        seen["path"] = req.url.path
        seen["q"] = parse_qs(req.url.query.decode())
        return httpx.Response(200, text='[{"favicon": "https://fm4.orf.at/logo.png", "url_resolved": "x"}]')

    logo = rb(h).logo_for("https://orf-live.ors-shoutcast.at/fm4-q2a")
    assert logo == "https://fm4.orf.at/logo.png"
    assert seen["path"] == "/json/stations/byurl" and seen["q"]["url"] == ["https://orf-live.ors-shoutcast.at/fm4-q2a"]
    assert rb(lambda r: httpx.Response(200, text="[]")).logo_for("http://x") is None



def test_is_hls():
    from deskremote.core.radio import is_hls
    assert is_hls("http://x/live/bbc_6music.m3u8")
    assert is_hls("https://x/a.M3U8?token=1&b=2")
    assert not is_hls("http://x/playlist.m3u")
    assert not is_hls("http://orf-live.ors-shoutcast.at/fm4-q2a")
    assert not is_hls("http://x/m3u8-info/stream.mp3")


def test_station_hls_flag():
    import json
    data = json.dumps([
        {"stationuuid": "1", "name": "A", "url_resolved": "http://a/x.m3u8", "hls": 0},
        {"stationuuid": "2", "name": "B", "url_resolved": "http://b/stream", "hls": 1},
        {"stationuuid": "3", "name": "C", "url_resolved": "http://c/stream.mp3", "hls": 0},
    ])
    res = rb(lambda r: httpx.Response(200, text=data)).search("x")
    assert [s.hls for s in res] == [True, True, False]
