"""Minimaler synchroner UPnP-SOAP-Client."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape

import httpx

from ..log import QUIET_ACTIONS, get_logger, short
from .xmlsafe import XmlError, parse

AVT = "urn:schemas-upnp-org:service:AVTransport:1"
RC = "urn:schemas-upnp-org:service:RenderingControl:1"

_DEV_NS = "{urn:schemas-upnp-org:device-1-0}"
TIMEOUT = 3.0


class UpnpError(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class UpnpDevice:
    def __init__(self, location: str, services: dict[str, str], client: httpx.Client):
        self.location = location
        self.services = services
        self.client = client

    @classmethod
    def load(cls, location: str, client: httpx.Client) -> "UpnpDevice":
        try:
            resp = client.get(location, timeout=TIMEOUT)
            resp.raise_for_status()
            root = parse(resp.content)
        except (httpx.HTTPError, XmlError) as exc:
            raise UpnpError(f"Gerätebeschreibung nicht lesbar: {location}: {exc}") from exc
        services: dict[str, str] = {}
        for svc in root.iter(f"{_DEV_NS}service"):
            st = svc.findtext(f"{_DEV_NS}serviceType")
            ctrl = svc.findtext(f"{_DEV_NS}controlURL")
            if not (st and ctrl):
                continue
            resolved = urljoin(location, ctrl.strip())
            parts, origin = urlsplit(resolved), urlsplit(location)
            if parts.scheme in ("http", "https") and parts.netloc == origin.netloc:
                services[st.strip()] = resolved
        return cls(location, services, client)

    def call(self, service: str, action: str, **args: str | int) -> dict[str, str]:
        url = self.services.get(service)
        if url is None:
            raise UpnpError(f"Dienst {service} nicht vorhanden")
        params = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args.items())
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{service}">{params}</u:{action}>'
            "</s:Body></s:Envelope>"
        )
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{service}#{action}"',
        }
        log = get_logger()
        loud = action not in QUIET_ACTIONS
        if loud:
            shown = {k: ("<DIDL…>" if k.endswith("MetaData") else short(v, 80)) for k, v in args.items()}
            log.info("UPnP %s %s %s", urlsplit(url).netloc, action, shown)
        try:
            resp = self.client.post(url, content=body.encode("utf-8"), headers=headers, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            log.warning("UPnP %s %s fehlgeschlagen: %s", urlsplit(url).netloc, action, short(exc))
            raise UpnpError(f"{action}: {exc}") from exc
        try:
            root = parse(resp.content)
        except XmlError as exc:
            raise UpnpError(f"{action}: ungültige Antwort (HTTP {resp.status_code})") from exc
        fault = next((e for e in root.iter() if _local(e.tag) == "Fault"), None)
        if fault is not None or resp.status_code >= 400:
            code = next((e.text for e in root.iter() if _local(e.tag) == "errorCode"), "")
            desc = next((e.text for e in root.iter() if _local(e.tag) == "errorDescription"), "")
            log.warning("UPnP %s %s abgelehnt: %s %s", urlsplit(url).netloc, action, short(code, 20), short(desc))
            raise UpnpError(f"{action} fehlgeschlagen: {code} {desc}".strip())
        response = next((e for e in root.iter() if _local(e.tag) == f"{action}Response"), None)
        if response is None:
            return {}
        return {_local(c.tag): (c.text or "") for c in response}
