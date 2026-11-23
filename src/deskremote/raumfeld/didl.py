"""DIDL-Lite-Metadaten für Radio-Streams."""

from __future__ import annotations

from xml.sax.saxutils import escape

from ..core.text import clean_text
from .xmlsafe import XmlError, parse

_DC = "{http://purl.org/dc/elements/1.1/}"
_UPNP = "{urn:schemas-upnp-org:metadata-1-0/upnp/}"


def didl_for_stream(title: str, url: str) -> str:
    title, url = clean_text(title, 200), clean_text(url)
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="deskremote-stream" parentID="0" restricted="1">'
        f"<dc:title>{escape(title)}</dc:title>"
        "<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>"
        f'<res protocolInfo="http-get:*:audio/mpeg:*">{escape(url)}</res>'
        "</item></DIDL-Lite>"
    )


def track_from_didl(didl: str) -> tuple[str | None, str | None, str | None]:
    """(Titel, Interpret, Cover-Adresse) eines laufenden Titels; fehlt etwas → None."""
    if not didl:
        return None, None, None
    try:
        root = parse(didl)
    except XmlError:
        return None, None, None

    def first(*tags: str) -> str | None:
        for tag in tags:
            el = root.find(f".//{tag}")
            if el is not None and el.text:
                return el.text
        return None

    def cleaned(value: str | None, limit: int | None) -> str | None:
        return (clean_text(value, limit) or None) if value else None

    return (cleaned(first(f"{_DC}title"), 200), cleaned(first(f"{_UPNP}artist", f"{_DC}creator"), 200),
            cleaned(first(f"{_UPNP}albumArtURI"), None))


def item_id_from_didl(didl: str) -> str | None:
    """Objekt-ID des laufenden Titels (bei Raumfeld die ID in der Bibliothek)."""
    if not didl:
        return None
    try:
        root = parse(didl)
    except XmlError:
        return None
    el = next((e for e in root if e.tag.rsplit("}", 1)[-1] == "item"), None)
    return (el.get("id") or None) if el is not None else None


def title_from_didl(didl: str) -> str | None:
    if not didl:
        return None
    try:
        root = parse(didl)
    except XmlError:
        return None
    for tag in (f"{_UPNP}stationName", f"{_DC}title"):
        el = root.find(f".//{tag}")
        if el is not None and el.text:
            return el.text
    return None
