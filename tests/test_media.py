from pathlib import Path

import pytest

from deskremote.raumfeld import discovery
from deskremote.raumfeld.media import (
    ALBUMS, CD, MediaContainer, MediaServer, MediaTrack, container_uri, parse_didl_page,
)
from deskremote.raumfeld.xmlsafe import XmlError

FIX = Path(__file__).parent / "fixtures"
ALBUMS_XML = (FIX / "media_albums.xml").read_text(encoding="utf-8")
TRACKS_XML = (FIX / "media_album_tracks.xml").read_text(encoding="utf-8")
MS_UDN = "uuid:c24a80f6-f43a-51a1-9031-97cedeb3c9f3"


def test_parse_albums_fixture():
    page = parse_didl_page(ALBUMS_XML, total=440, start=0)
    first = page.items[0]
    assert isinstance(first, MediaContainer)
    assert first.title == "Album 01" and first.artist == "Naked Lunch"
    assert first.child_count == 9 and first.kind == "album"
    assert first.art_url.startswith("http://192.168.0.176:47366/")
    assert first.id == "0/My Music/Albums/Naked Lunch+Album%2001"
    assert page.total == 440 and len(page.items) == 20


def test_parse_tracks_fixture():
    page = parse_didl_page(TRACKS_XML, total=9, start=0)
    assert len(page.items) == 9
    t = page.items[0]
    assert isinstance(t, MediaTrack)
    assert t.title == "Titel 03" and t.number == 1 and t.duration == "0:06:50.000"
    assert t.artist == "Naked Lunch" and t.album == "Album 01"
    assert t.url.startswith("http://192.168.0.176:")


def test_container_uri_quotes_real_id():
    assert container_uri(MS_UDN, "0/My Music/Albums/Naked Lunch+Album%2001", 2) == (
        "dlna-playcontainer://uuid%3Ac24a80f6-f43a-51a1-9031-97cedeb3c9f3"
        "?sid=urn%3Aupnp-org%3AserviceId%3AContentDirectory"
        "&cid=0%2FMy%20Music%2FAlbums%2FNaked%20Lunch%2BAlbum%252001&md=0&fii=2"
    )


class FakeDevice:
    def __init__(self, total=250, didl=ALBUMS_XML):
        self.calls = []
        self.total = total
        self.didl = didl

    def call(self, service, action, **args):
        self.calls.append((service, action, args))
        if action == "Browse" and args.get("BrowseFlag") == "BrowseMetadata":
            return {"Result": "<DIDL-Lite/>", "NumberReturned": "1", "TotalMatches": "1"}
        return {"Result": self.didl, "NumberReturned": "20", "TotalMatches": str(self.total)}


def test_browse_pages():
    dev = FakeDevice(total=250)
    ms = MediaServer(MS_UDN, "Raumfeld MediaServer", dev, raumfeld=True)
    p1 = ms.browse(ALBUMS)
    p2 = ms.browse(ALBUMS, start=100)
    assert p1.total == 250 and p2.start == 100
    starts = [(a["StartingIndex"], a["RequestedCount"]) for s, act, a in dev.calls]
    assert starts == [(0, 100), (100, 100)]
    assert all(s == CD and act == "Browse" for s, act, _ in dev.calls)
    assert ms.is_raumfeld is True


def test_metadata_uses_browse_metadata():
    dev = FakeDevice()
    ms = MediaServer(MS_UDN, "Raumfeld MediaServer", dev)
    assert ms.metadata("0/x") == "<DIDL-Lite/>"
    assert dev.calls[-1][2]["BrowseFlag"] == "BrowseMetadata"


def test_search_escapes_quotes():
    dev = FakeDevice()
    ms = MediaServer(MS_UDN, "DS923", dev)
    ms.search("0/My Music/Search", 'Rock "n" Roll\\')
    crit = dev.calls[-1][2]["SearchCriteria"]
    assert crit == 'dc:title contains "Rock \\"n\\" Roll\\\\"'
    assert dev.calls[-1][1] == "Search"
    assert ms.is_raumfeld is False


def test_parse_rejects_entities():
    with pytest.raises(XmlError):
        parse_didl_page('<!DOCTYPE r [<!ENTITY a "x">]><DIDL-Lite>&a;</DIDL-Lite>', 0, 0)


def test_ssdp_search_all_collects_every_server(monkeypatch):
    st = "urn:schemas-upnp-org:device:MediaServer:1"
    monkeypatch.setattr(discovery, "_local_ipv4", lambda: ["192.168.0.30", "172.17.0.1"])
    seen = []

    def fake_all(ip, timeout, st_arg):
        seen.append((ip, st_arg))
        return ["http://192.168.0.176:1/a.xml", "http://192.168.0.29:2/b.xml"] if ip == "192.168.0.30" else []

    monkeypatch.setattr(discovery, "_ssdp_collect", fake_all)
    assert discovery.ssdp_search_all(st, 0.1) == ["http://192.168.0.176:1/a.xml", "http://192.168.0.29:2/b.xml"]
    assert seen == [("192.168.0.30", st), ("172.17.0.1", st)]


def test_accept_ssdp_with_other_st():
    data = (b"HTTP/1.1 200 OK\r\nLocation: http://192.168.0.29:50001/desc.xml\r\n"
            b"ST: urn:schemas-upnp-org:device:MediaServer:1\r\n\r\n")
    assert discovery.accept_ssdp(data, "192.168.0.29", st="urn:schemas-upnp-org:device:MediaServer:1")
    assert discovery.accept_ssdp(data, "192.168.0.29") is None  # Standard bleibt ConfigDevice


def test_search_album_constants_and_real_result():
    from deskremote.raumfeld.media import SEARCH_ALBUMS, SEARCH_ARTISTS
    assert SEARCH_ALBUMS == "0/My Music/Search/Albums"
    assert SEARCH_ARTISTS == "0/My Music/Search/TrackArtists"
    xml = (FIX / "media_search_albums_love.xml").read_text(encoding="utf-8")
    titles = [i.title for i in parse_didl_page(xml, 4, 0).items]
    assert "Dummy Love" in titles and all(isinstance(i, MediaContainer) for i in parse_didl_page(xml, 4, 0).items)


def test_ssdp_collect_has_overall_deadline_and_cap(monkeypatch):
    import time as _t

    class Flood:
        def __init__(self):
            self.n = 0

        def sendto(self, *a):
            pass

        def settimeout(self, t):
            pass

        def recvfrom(self, size):
            self.n += 1
            ip = f"192.168.0.{(self.n % 200) + 2}"
            return (f"HTTP/1.1 200 OK\r\nLocation: http://{ip}:{self.n}/d.xml\r\n"
                    f"ST: {st}\r\n\r\n").encode(), (ip, 1900)

        def close(self):
            pass

    st = "urn:schemas-upnp-org:device:MediaServer:1"
    monkeypatch.setattr(discovery, "_open", lambda ip, timeout: Flood())
    t = _t.monotonic()
    found = discovery._ssdp_collect("192.168.0.30", 0.2, st)
    assert _t.monotonic() - t < 1.0
    assert len(found) <= discovery.MAX_SSDP_RESULTS


def test_raumfeld_trust_comes_from_host_not_from_name(monkeypatch):
    from deskremote.raumfeld import media
    from deskremote.raumfeld.models import MEDIA_SERVER, Device
    known = [Device(MS_UDN, MEDIA_SERVER, "http://192.168.0.176:60406/x.xml", "Raumfeld MediaServer")]
    monkeypatch.setattr(media, "ssdp_search_all", lambda st, timeout: ["http://192.168.0.66:1/fake.xml"])
    monkeypatch.setattr(media, "_udn", lambda loc, client: "uuid:fake")
    monkeypatch.setattr(media, "_friendly_name", lambda loc, client: "Raumfeld MediaServer")
    servers = media.discover_media_servers(None, known)
    assert [(s.udn, s.trusted) for s in servers] == [(MS_UDN, True), ("uuid:fake", False)]
    assert MediaServer("uuid:fake", "Raumfeld MediaServer", FakeDevice(), raumfeld=False).is_raumfeld is False


def test_ssdp_collect_limits_entries_per_host(monkeypatch):
    st = "urn:schemas-upnp-org:device:MediaServer:1"
    answers = [("192.168.0.66", p) for p in range(1, 40)] + [("192.168.0.29", 5000)]

    class Sock:
        def sendto(self, *a):
            pass

        def settimeout(self, t):
            pass

        def recvfrom(self, size):
            if not answers:
                raise OSError("timeout")
            ip, port = answers.pop(0)
            return (f"HTTP/1.1 200 OK\r\nLocation: http://{ip}:{port}/d.xml\r\nST: {st}\r\n\r\n").encode(), (ip, 1900)

        def close(self):
            pass

    monkeypatch.setattr(discovery, "_open", lambda ip, timeout: Sock())
    found = discovery._ssdp_collect("192.168.0.30", 1.0, st)
    assert sum("192.168.0.66" in f for f in found) <= 2
    assert "http://192.168.0.29:5000/d.xml" in found


def test_search_hit_normalized_to_album_with_local_art():
    from deskremote.raumfeld.media import normalize_raumfeld_album
    c = MediaContainer("0/My Music/Search/Albums/Sophie Hunger+Dummy%20Love", "Dummy Love", "Sophie Hunger",
                       10, None, "album")
    n = normalize_raumfeld_album(c, "192.168.0.176")
    assert n.id == "0/My Music/Albums/Sophie Hunger+Dummy%20Love"
    assert n.art_url == "http://192.168.0.176:47366/?albumArtist=Sophie%20Hunger&album=Dummy%20Love"
    keep = MediaContainer("0/My Music/Albums/X", "X", "Y", 1, "http://192.168.0.176:47366/?a=1", "album")
    assert normalize_raumfeld_album(keep, "192.168.0.176") == keep
    real = parse_didl_page(ALBUMS_XML, 1, 0).items[0]
    assert normalize_raumfeld_album(MediaContainer(real.id, real.title, real.artist, 9, None, "album"),
                                    "192.168.0.176").art_url == real.art_url  # Schema stimmt mit echter Adresse überein



def test_normalize_artist_search_hit():
    from deskremote.raumfeld.media import normalize_raumfeld_album
    c = MediaContainer("0/My Music/Search/TrackArtists/Die Sterne", "Die Sterne", "Die Sterne", 1, None, "artist")
    assert normalize_raumfeld_album(c, "192.168.0.176").id == "0/My Music/Artists/Die Sterne"



def test_with_start_index_replaces_fii():
    from deskremote.raumfeld.media import with_start_index
    uri = "dlna-playcontainer://u?sid=s&cid=c&md=0&fii=0"
    assert with_start_index(uri, 7) == "dlna-playcontainer://u?sid=s&cid=c&md=0&fii=7"
    assert with_start_index("dlna-playcontainer://u?sid=s&cid=c", 3) == "dlna-playcontainer://u?sid=s&cid=c"
