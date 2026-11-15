"""Raumfeld-Host im Netz finden: zuletzt bekannte Adresse, sonst SSDP."""

from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urlparse

import httpx

from .host import RaumfeldHost
from .models import CONFIG_DEVICE
from .xmlsafe import XmlError

HOST_PORT = 47365
MAX_SSDP_RESULTS = 32
MAX_SSDP_PER_HOST = 2  # mehr Medienserver hat kein Heimnetz – schützt vor Antwortfluten
_SSDP_ADDR = ("239.255.255.250", 1900)
_CRLF = "\r\n"


def parse_ssdp_location(data: bytes) -> str | None:
    for line in data.decode("utf-8", "replace").splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() == "location" and value.strip():
            return value.strip()
    return None


def accept_ssdp(data: bytes, src_ip: str, st: str = CONFIG_DEVICE) -> str | None:
    """Nimmt eine SSDP-Antwort nur an, wenn ST passt und LOCATION auf den privaten Absender zeigt."""
    headers = {}
    for line in data.decode("utf-8", "replace").splitlines():
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    if headers.get("st") != st:
        return None
    loc = headers.get("location", "")
    parsed = urlparse(loc)
    if parsed.scheme != "http" or parsed.hostname != src_ip:
        return None
    try:
        if not ipaddress.ip_address(src_ip).is_private:
            return None
    except ValueError:
        return None
    return loc


def _local_ipv4() -> list[str]:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({i[4][0] for i in infos if not i[4][0].startswith("127.")})


def _msearch(st: str) -> bytes:
    return _CRLF.join([
        "M-SEARCH * HTTP/1.1",
        f"HOST: {_SSDP_ADDR[0]}:{_SSDP_ADDR[1]}",
        'MAN: "ssdp:discover"',
        "MX: 1",
        f"ST: {st}",
        "",
        "",
    ]).encode()


def _open(ip: str, timeout: float) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.bind((ip, 0))
    sock.settimeout(timeout)
    return sock


def _ssdp_collect(ip: str, timeout: float, st: str) -> list[str]:
    """Alle passenden Antworten auf einer Schnittstelle sammeln, bis die Zeit um ist."""
    found: list[str] = []
    try:
        sock = _open(ip, timeout)
    except OSError:
        return found
    deadline = time.monotonic() + timeout  # Gesamtfrist, nicht nur pro Antwort
    per_host: dict[str, int] = {}  # ein Gerät darf nicht alle Plätze belegen
    try:
        sock.sendto(_msearch(st), _SSDP_ADDR)
        while len(found) < MAX_SSDP_RESULTS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            data, (src_ip, _port) = sock.recvfrom(4096)
            loc = accept_ssdp(data, src_ip, st)
            if loc and loc not in found and per_host.get(src_ip, 0) < MAX_SSDP_PER_HOST:
                found.append(loc)
                per_host[src_ip] = per_host.get(src_ip, 0) + 1
        return found
    except OSError:
        return found
    finally:
        sock.close()


def ssdp_search_all(st: str, timeout: float) -> list[str]:
    """Alle Geräte eines Typs über jede lokale IPv4-Schnittstelle finden."""
    result: list[str] = []
    for ip in _local_ipv4():
        for loc in _ssdp_collect(ip, timeout, st):
            if loc not in result:
                result.append(loc)
    return result


def _ssdp_on(ip: str, timeout: float) -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind((ip, 0))
        sock.settimeout(timeout)
        sock.sendto(_msearch(CONFIG_DEVICE), _SSDP_ADDR)
        while True:
            data, (src_ip, _port) = sock.recvfrom(4096)
            loc = accept_ssdp(data, src_ip)
            if loc:
                return loc
    except OSError:
        return None
    finally:
        sock.close()


def ssdp_search(timeout: float) -> str | None:
    """Sucht über jede lokale IPv4-Schnittstelle (sonst wählt Windows evtl. Hyper-V/Docker-Adapter)."""
    for ip in _local_ipv4():
        loc = _ssdp_on(ip, timeout)
        if loc:
            return loc
    return None


def _reachable(base_url: str, client: httpx.Client) -> bool:
    try:
        RaumfeldHost(base_url, client).get_zones()
        return True
    except (httpx.HTTPError, XmlError):
        return False


def discover_host(last_known: str | None, client: httpx.Client, ssdp_timeout: float = 2.0) -> str | None:
    if last_known and _reachable(last_known, client):
        return last_known
    loc = ssdp_search(ssdp_timeout)
    if not loc:
        return None
    host = urlparse(loc).hostname
    candidate = f"http://{host}:{HOST_PORT}"
    return candidate if _reachable(candidate, client) else None
