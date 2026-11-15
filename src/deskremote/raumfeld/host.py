"""Client für die Raumfeld-Host-API (Port 47365)."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

import httpx

from ..log import QUIET_ACTIONS, get_logger
from .models import Device, ZoneConfig, parse_devices, parse_zone_config

TIMEOUT = 3.0
LONG_POLL_TIMEOUT = 60.0
MAX_REDIRECTS = 3


class ForeignRedirect(httpx.HTTPError):
    """Weiterleitung auf ein anderes Gerät – wird nicht verfolgt."""


def same_origin_get(client: httpx.Client, url: str, *, params: dict | None = None,
                    headers: dict | None = None, timeout: float = TIMEOUT) -> httpx.Response:
    """GET, der nur Weiterleitungen auf denselben Host/Port verfolgt (Raumfeld-Sitzungspfad)."""
    origin = urlsplit(url)
    for _ in range(MAX_REDIRECTS + 1):
        resp = client.get(url, params=params, headers=headers, timeout=timeout, follow_redirects=False)
        if not resp.is_redirect:
            resp.raise_for_status()
            return resp
        target = urljoin(url, resp.headers.get("Location", ""))
        parts = urlsplit(target)
        if (parts.scheme, parts.netloc) != (origin.scheme, origin.netloc):
            raise ForeignRedirect(f"Weiterleitung auf fremden Host abgelehnt: {parts.netloc}")
        url, params = target, None
    raise ForeignRedirect("Zu viele Weiterleitungen")


class RaumfeldHost:
    def __init__(self, base_url: str, client: httpx.Client):
        self.base_url = base_url.rstrip("/")
        self.client = client

    def _get(self, path: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: float = TIMEOUT) -> httpx.Response:
        log = get_logger()
        loud = path not in QUIET_ACTIONS
        try:
            resp = same_origin_get(self.client, f"{self.base_url}/{path}", params=params,
                                   headers=headers, timeout=timeout)
        except httpx.HTTPStatusError as exc:
            log.warning("HOST %s %s -> HTTP %s", path, params or "", exc.response.status_code)
            raise
        except httpx.HTTPError as exc:
            if loud:
                log.warning("HOST %s %s -> %s", path, params or "", type(exc).__name__)
            raise
        if loud:
            log.info("HOST %s %s -> HTTP %s", path, params or "", resp.status_code)
        return resp

    def get_zones(self, update_id: str | None = None) -> tuple[ZoneConfig, str]:
        if update_id is None:
            resp = self._get("getZones")
        else:
            resp = self._get("getZones", headers={"updateID": update_id}, timeout=LONG_POLL_TIMEOUT)
        return parse_zone_config(resp.text), resp.headers.get("updateID", "")

    def list_devices(self) -> dict[str, Device]:
        return parse_devices(self._get("listDevices").text)

    def connect_rooms_to_zone(self, room_udns: list[str], zone_udn: str | None = None) -> None:
        self._get("connectRoomsToZone", params={"zoneUDN": zone_udn or "", "roomUDNs": ",".join(room_udns)})

    def drop_room(self, room_udn: str) -> None:
        self._get("dropRoomJob", params={"roomUDN": room_udn})
