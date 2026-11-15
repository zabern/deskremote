from pathlib import Path

import httpx

from deskremote.raumfeld import discovery
from deskremote.raumfeld.discovery import discover_host, parse_ssdp_location

ZONES = (Path(__file__).parent / "fixtures" / "getZones.xml").read_text(encoding="utf-8")


def test_parse_ssdp_location():
    data = (
        b"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\n"
        b"LOCATION: http://192.168.0.176:54089/2b77.xml\r\n"
        b"ST: urn:schemas-raumfeld-com:device:ConfigDevice:1\r\n\r\n"
    )
    assert parse_ssdp_location(data) == "http://192.168.0.176:54089/2b77.xml"
    assert parse_ssdp_location(b"HTTP/1.1 200 OK\r\nST: x\r\n\r\n") is None


def test_discover_uses_last_known_when_reachable(monkeypatch):
    monkeypatch.setattr(discovery, "ssdp_search", lambda timeout: (_ for _ in ()).throw(AssertionError("kein SSDP")))
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=ZONES)))
    assert discover_host("http://192.168.0.176:47365", client) == "http://192.168.0.176:47365"


def test_discover_falls_back_to_ssdp(monkeypatch):
    monkeypatch.setattr(discovery, "ssdp_search", lambda timeout: "http://192.168.0.180:54089/x.xml")

    def h(req):
        if req.url.host == "192.168.0.176":
            raise httpx.ConnectError("weg")
        return httpx.Response(200, text=ZONES)

    client = httpx.Client(transport=httpx.MockTransport(h))
    assert discover_host("http://192.168.0.176:47365", client) == "http://192.168.0.180:47365"


def test_discover_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr(discovery, "ssdp_search", lambda timeout: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert discover_host(None, client) is None


def test_ssdp_search_tries_every_interface(monkeypatch):
    tried = []
    monkeypatch.setattr(discovery, "_local_ipv4", lambda: ["172.17.0.1", "192.168.0.30"])
    monkeypatch.setattr(
        discovery, "_ssdp_on", lambda ip, timeout: tried.append(ip) or ("http://192.168.0.176:1/x.xml" if ip == "192.168.0.30" else None)
    )
    assert discovery.ssdp_search(0.1) == "http://192.168.0.176:1/x.xml"
    assert tried == ["172.17.0.1", "192.168.0.30"]


SSDP_OK = (
    b"HTTP/1.1 200 OK\r\nLocation: http://192.168.0.176:54089/x.xml\r\n"
    b"ST: urn:schemas-raumfeld-com:device:ConfigDevice:1\r\n\r\n"
)


def test_accept_ssdp_valid():
    assert discovery.accept_ssdp(SSDP_OK, "192.168.0.176") == "http://192.168.0.176:54089/x.xml"


def test_accept_ssdp_rejects_spoofed_location():
    assert discovery.accept_ssdp(SSDP_OK, "192.168.0.99") is None


def test_accept_ssdp_rejects_wrong_st_and_public_ip():
    assert discovery.accept_ssdp(SSDP_OK.replace(b"ConfigDevice", b"Other"), "192.168.0.176") is None
    public = SSDP_OK.replace(b"192.168.0.176", b"8.8.8.8")
    assert discovery.accept_ssdp(public, "8.8.8.8") is None


def test_reachable_requires_real_zone_config(monkeypatch):
    monkeypatch.setattr(discovery, "ssdp_search", lambda timeout: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>zoneConfig</html>")))
    assert discover_host("http://192.168.0.176:47365", client) is None
