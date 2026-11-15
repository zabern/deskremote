from pathlib import Path
from urllib.parse import parse_qs

import httpx

from deskremote.raumfeld.host import RaumfeldHost

FIX = Path(__file__).parent / "fixtures"
ZONES = (FIX / "getZones.xml").read_text(encoding="utf-8")
DEVICES = (FIX / "listDevices.xml").read_text(encoding="utf-8")


def host_with(handler):
    return RaumfeldHost("http://10.0.0.5:47365", httpx.Client(transport=httpx.MockTransport(handler)))


def session_redirect(inner):
    """Simuliert die Raumfeld-Eigenheit: erst 307 auf /<sitzung>/<pfad>."""

    def h(req):
        if not req.url.path.startswith("/sess/"):
            return httpx.Response(307, headers={"Location": "/sess" + req.url.path + ("?" + req.url.query.decode() if req.url.query else "")})
        return inner(req)

    return h


def test_get_zones_follows_redirect_and_returns_update_id():
    host = host_with(session_redirect(lambda r: httpx.Response(200, text=ZONES, headers={"updateID": "2779843690"})))
    zc, uid = host.get_zones()
    assert len(zc.zones) == 2
    assert uid == "2779843690"


def test_long_poll_sends_update_id_header():
    seen = {}

    def inner(req):
        seen["uid"] = req.headers.get("updateID")
        return httpx.Response(200, text=ZONES, headers={"updateID": "2"})

    host_with(session_redirect(inner)).get_zones(update_id="1")
    assert seen["uid"] == "1"


def test_list_devices():
    devs = host_with(session_redirect(lambda r: httpx.Response(200, text=DEVICES))).list_devices()
    assert devs["uuid:53c6005e-fe3b-5e5f-b1a0-17a8343aa163"].name == "Badezimmer"


def test_connect_rooms_builds_query():
    seen = {}

    def inner(req):
        seen["path"] = req.url.path
        seen["q"] = parse_qs(req.url.query.decode(), keep_blank_values=True)
        return httpx.Response(200)

    host_with(session_redirect(inner)).connect_rooms_to_zone(["uuid:a", "uuid:b"])
    assert seen["path"].endswith("/connectRoomsToZone")
    assert seen["q"]["zoneUDN"] == [""]
    assert seen["q"]["roomUDNs"] == ["uuid:a,uuid:b"]


def test_connect_rooms_to_existing_zone():
    seen = {}

    def inner(req):
        seen["q"] = parse_qs(req.url.query.decode(), keep_blank_values=True)
        return httpx.Response(200)

    host_with(session_redirect(inner)).connect_rooms_to_zone(["uuid:a"], zone_udn="uuid:z")
    assert seen["q"]["zoneUDN"] == ["uuid:z"]


def test_drop_room_builds_query():
    seen = {}

    def inner(req):
        seen["path"] = req.url.path
        seen["q"] = parse_qs(req.url.query.decode())
        return httpx.Response(200)

    host_with(session_redirect(inner)).drop_room("uuid:a")
    assert seen["path"].endswith("/dropRoomJob")
    assert seen["q"]["roomUDN"] == ["uuid:a"]


def test_redirect_to_other_host_is_refused():
    import pytest
    def h(req):
        if req.url.host == "evil.example":
            return httpx.Response(200, text=ZONES)
        return httpx.Response(307, headers={"Location": "http://evil.example/getZones"})

    host = host_with(h)
    with pytest.raises(httpx.HTTPError):
        host.get_zones()
