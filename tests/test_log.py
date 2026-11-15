import logging

import httpx

from deskremote.log import setup_logging
from deskremote.raumfeld.host import RaumfeldHost
from deskremote.raumfeld.upnp import AVT, UpnpDevice

DESC = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><serviceList>
<service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
<controlURL>/ctrl</controlURL></service></serviceList></device></root>"""
OK = """<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
<u:PlayResponse xmlns:u="urn:schemas-upnp-org:service:AVTransport:1"/></s:Body></s:Envelope>"""


def read(path):
    for h in logging.getLogger("deskremote").handlers:
        h.flush()
    return path.read_text(encoding="utf-8")


def test_commands_are_logged_but_not_status_polls(tmp_path):
    log = setup_logging(tmp_path / "rf.log")

    def h(req):
        return httpx.Response(200, text=DESC if req.url.path == "/d.xml" else OK)

    dev = UpnpDevice.load("http://10.0.0.9:5000/d.xml", httpx.Client(transport=httpx.MockTransport(h)))
    dev.call(AVT, "Play", InstanceID=0, Speed=1)
    dev.call(AVT, "GetTransportInfo", InstanceID=0)
    text = read(log)
    assert "Play" in text and "10.0.0.9:5000" in text
    assert "GetTransportInfo" not in text


def test_host_commands_and_errors_are_logged(tmp_path):
    log = setup_logging(tmp_path / "rf.log")
    host = RaumfeldHost("http://10.0.0.5:47365", httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(500) if "dropRoomJob" in r.url.path else httpx.Response(200))))
    host.connect_rooms_to_zone(["uuid:a", "uuid:b"])
    try:
        host.drop_room("uuid:a")
    except httpx.HTTPError:
        pass
    text = read(log)
    assert "connectRoomsToZone" in text and "uuid:a,uuid:b" in text
    assert "dropRoomJob" in text and "500" in text


def test_setup_is_idempotent(tmp_path):
    setup_logging(tmp_path / "a.log")
    setup_logging(tmp_path / "b.log")
    assert len(logging.getLogger("deskremote").handlers) == 1


def test_log_values_are_sanitized():
    from deskremote.log import short
    assert short("a\nFAKE 2026 INFO alles gut\r\x1b[31m") == "aFAKE 2026 INFO alles gut[31m"
    assert short("http://user:geheim@stream.example/x") == "http://stream.example/x"
    assert len(short("x" * 500, 50)) <= 50


def test_rejection_text_from_device_cannot_forge_lines(tmp_path):
    log = setup_logging(tmp_path / "rf.log")
    fault = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
             '<s:Fault><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>701</errorCode>'
             '<errorDescription>boom&#10;2026-01-01 INFO gefälscht</errorDescription></UPnPError></detail>'
             '</s:Fault></s:Body></s:Envelope>')

    def h(req):
        return httpx.Response(200, text=DESC) if req.url.path == "/d.xml" else httpx.Response(500, text=fault)

    dev = UpnpDevice.load("http://10.0.0.9:5000/d.xml", httpx.Client(transport=httpx.MockTransport(h)))
    try:
        dev.call(AVT, "Play", InstanceID=0, Speed=1)
    except Exception:
        pass
    lines = read(log).splitlines()
    assert not any(line.startswith("2026-01-01 INFO") for line in lines)


def test_short_handles_hidden_tricks():
    from deskremote.log import short
    assert short("rtsp://u:p@cam.example/x") == "rtsp://cam.example/x"
    assert short("http://us\x00er:pw@host/x") == "http://host/x"
    assert "\u2028" not in short("a\u2028b\u2029c") and "\u2029" not in short("a\u2028b\u2029c")


def test_password_containing_at_sign_is_fully_redacted():
    from deskremote.log import short
    assert short("http://user:p@ss@host.example/x?a=b@c") == "http://host.example/x?a=b@c"


def test_question_mark_or_hash_in_userinfo_is_redacted():
    from deskremote.log import short
    assert short("http://u:p?x#y@host.example/z") == "http://host.example/z"
    assert short("http://host.example/x?a=b@c") == "http://host.example/x?a=b@c"
