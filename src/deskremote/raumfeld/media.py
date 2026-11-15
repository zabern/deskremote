"""UPnP-Medienserver (Raumfeld-Bibliothek und andere DLNA-Server): blättern, suchen, Alben abspielen."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx

from .discovery import ssdp_search_all
from .models import MEDIA_SERVER, Device
from .upnp import UpnpDevice, UpnpError
from .xmlsafe import parse

CD = "urn:schemas-upnp-org:service:ContentDirectory:1"
MEDIA_SERVER_ST = MEDIA_SERVER
PAGE_SIZE = 100

# Raumfeld-Bibliothek
ALBUMS = "0/My Music/Albums"
ARTISTS = "0/My Music/Artists"
FOLDERS = "0/My Music/ByFolder"
SEARCH = "0/My Music/Search"
SEARCH_ALBUMS = "0/My Music/Search/Albums"  # Suche liefert nur dort Treffer (an der Anlage geprüft)
SEARCH_ARTISTS = "0/My Music/Search/TrackArtists"

_DIDL = "{urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_UPNP = "{urn:schemas-upnp-org:metadata-1-0/upnp/}"


@dataclass
class MediaContainer:
    id: str
    title: str
    artist: str | None
    child_count: int | None
    art_url: str | None
    kind: str  # "album", "artist" oder "folder"


@dataclass
class MediaTrack:
    id: str
    title: str
    artist: str | None
    album: str | None
    number: int | None
    duration: str | None
    art_url: str | None
    url: str | None


@dataclass
class ServerInfo:
    udn: str
    name: str
    location: str
    trusted: bool  # True nur, wenn die Raumfeld-Anlage selbst ihn als ihren Medienserver führt


@dataclass
class Page:
    items: list[MediaContainer | MediaTrack]
    total: int
    start: int


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _text(el, tag: str) -> str | None:
    found = el.find(tag)
    return found.text if found is not None and found.text else None


def _kind(upnp_class: str) -> str:
    if upnp_class.startswith("object.container.album"):
        return "album"
    if upnp_class.startswith("object.container.person"):
        return "artist"
    return "folder"


def parse_didl_page(didl: str, total: int, start: int) -> Page:
    root = parse(didl)
    items: list[MediaContainer | MediaTrack] = []
    for el in root:
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "container":
            items.append(MediaContainer(
                id=el.get("id", ""),
                title=_text(el, f"{_DC}title") or "",
                artist=_text(el, f"{_UPNP}artist"),
                child_count=_int(el.get("childCount")),
                art_url=_text(el, f"{_UPNP}albumArtURI"),
                kind=_kind(_text(el, f"{_UPNP}class") or ""),
            ))
        elif tag == "item":
            res = el.find(f"{_DIDL}res")
            items.append(MediaTrack(
                id=el.get("id", ""),
                title=_text(el, f"{_DC}title") or "",
                artist=_text(el, f"{_UPNP}artist") or _text(el, f"{_DC}creator"),
                album=_text(el, f"{_UPNP}album"),
                number=_int(_text(el, f"{_UPNP}originalTrackNumber")),
                duration=res.get("duration") if res is not None else None,
                art_url=_text(el, f"{_UPNP}albumArtURI"),
                url=res.text if res is not None else None,
            ))
    return Page(items=items, total=total, start=start)


def container_uri(server_udn: str, container_id: str, start_index: int) -> str:
    """Raumfeld spielt damit ein Album/einen Ordner selbst ab – ab Titel start_index (0-basiert)."""
    server = quote(server_udn, safe="")
    service = quote("urn:upnp-org:serviceId:ContentDirectory", safe="")
    return (f"dlna-playcontainer://{server}?sid={service}&cid={quote(container_id, safe='')}"
            f"&md=0&fii={int(start_index)}")


def with_start_index(uri: str, index: int) -> str:
    """Einstiegstitel einer dlna-playcontainer-Adresse ändern (fii=…); ohne fii unverändert."""
    head, sep, query = uri.partition("?")
    if not sep:
        return uri
    parts = [f"fii={int(index)}" if p.startswith("fii=") else p for p in query.split("&")]
    return head + "?" + "&".join(parts)


RAUMFELD_ART_PORT = 47366


def normalize_raumfeld_album(c: MediaContainer, host: str | None) -> MediaContainer:
    """Suchtreffer der Raumfeld-Bibliothek aufs normale Album zurückführen und fehlendes Cover ergänzen.

    An der Anlage beobachtet: Suchtreffer tragen `0/My Music/Search/Albums/<x>`, das Album heißt
    `0/My Music/Albums/<x>`; Cover-Adressen folgen `http://<One M>:47366/?albumArtist=…&album=…`.
    """
    if c.kind == "artist":
        prefix = SEARCH_ARTISTS + "/"
        if c.id.startswith(prefix):
            return MediaContainer(ARTISTS + "/" + c.id[len(prefix):], c.title, c.artist, c.child_count,
                                  c.art_url, c.kind)
        return c
    if c.kind != "album":
        return c
    prefix = SEARCH_ALBUMS + "/"
    new_id = ALBUMS + "/" + c.id[len(prefix):] if c.id.startswith(prefix) else c.id
    art = c.art_url
    if not art and host and c.artist:
        art = (f"http://{host}:{RAUMFELD_ART_PORT}/?albumArtist={quote(c.artist, safe='')}"
               f"&album={quote(c.title, safe='')}")
    if new_id == c.id and art == c.art_url:
        return c
    return MediaContainer(new_id, c.title, c.artist, c.child_count, art, c.kind)


def _escape_search(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


class MediaServer:
    def __init__(self, udn: str, name: str, device: UpnpDevice, raumfeld: bool = False):
        self.udn = udn
        self.name = name
        self.device = device
        self._raumfeld = raumfeld

    @property
    def is_raumfeld(self) -> bool:
        """Herkunft zählt, nicht der selbstgewählte Name eines Geräts."""
        return self._raumfeld

    @property
    def host(self) -> str | None:
        location = getattr(self.device, "location", "")
        return urlsplit(location).hostname if location else None

    def browse(self, object_id: str, start: int = 0, count: int = PAGE_SIZE) -> Page:
        out = self.device.call(CD, "Browse", ObjectID=object_id, BrowseFlag="BrowseDirectChildren",
                               Filter="*", StartingIndex=start, RequestedCount=count, SortCriteria="")
        return parse_didl_page(out.get("Result", ""), _int(out.get("TotalMatches")) or 0, start)

    def metadata(self, object_id: str) -> str:
        out = self.device.call(CD, "Browse", ObjectID=object_id, BrowseFlag="BrowseMetadata",
                               Filter="*", StartingIndex=0, RequestedCount=1, SortCriteria="")
        return out.get("Result", "")

    def search(self, container_id: str, text: str, start: int = 0, count: int = PAGE_SIZE) -> Page:
        criteria = f'dc:title contains "{_escape_search(text)}"'
        out = self.device.call(CD, "Search", ContainerID=container_id, SearchCriteria=criteria,
                               Filter="*", StartingIndex=start, RequestedCount=count, SortCriteria="")
        return parse_didl_page(out.get("Result", ""), _int(out.get("TotalMatches")) or 0, start)


def _friendly_name(location: str, client: httpx.Client) -> str | None:
    try:
        root = parse(client.get(location, timeout=3.0).content)
    except Exception:  # noqa: BLE001 – unerreichbare Quelle einfach auslassen
        return None
    el = next((e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "friendlyName"), None)
    return el.text.strip() if el is not None and el.text else None


def _udn(location: str, client: httpx.Client) -> str | None:
    try:
        root = parse(client.get(location, timeout=3.0).content)
    except Exception:  # noqa: BLE001
        return None
    el = next((e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "UDN"), None)
    return el.text.strip() if el is not None and el.text else None


def discover_media_servers(client: httpx.Client, known: list[Device], timeout: float = 2.0
                           ) -> list[ServerInfo]:
    """Alle Medienserver; die von der Raumfeld-Anlage geführten zuerst und als vertrauenswürdig markiert."""
    trusted = [ServerInfo(d.udn, d.name, d.location, True) for d in known if d.type == MEDIA_SERVER]
    seen = {s.udn for s in trusted}
    others: list[ServerInfo] = []
    for location in ssdp_search_all(MEDIA_SERVER_ST, timeout):
        udn = _udn(location, client)
        if not udn or udn in seen:
            continue
        name = _friendly_name(location, client) or urlsplit(location).hostname or "Medienserver"
        others.append(ServerInfo(udn, name, location, False))
        seen.add(udn)
    return trusted + sorted(others, key=lambda s: s.name.lower())


__all__ = [
    "ALBUMS", "ARTISTS", "CD", "FOLDERS", "MEDIA_SERVER_ST", "PAGE_SIZE", "SEARCH", "SEARCH_ALBUMS",
    "SEARCH_ARTISTS",
    "MediaContainer", "MediaServer", "MediaTrack", "Page", "ServerInfo", "UpnpError",
    "container_uri", "discover_media_servers", "normalize_raumfeld_album", "parse_didl_page",
    "with_start_index",
]
