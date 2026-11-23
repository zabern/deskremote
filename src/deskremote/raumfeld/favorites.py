"""Favoriten der Teufel-App: liegen im ContentDirectory des Raumfeld-Medienservers.

An der Anlage beobachtet (Raumfeld-Firmware, Protokoll 16351):
- Container `0/Favorites/MyFavorites`; jeder Eintrag ist ein Verweis (`refID`) auf ein Objekt der
  Anlage, z. B. einen TuneIn-Sender `0/RadioTime/Search/s-s8235` oder ein Album `0/My Music/…`.
- Sender tragen beim Blättern keine Stream-Adresse; die Teufel-App spielt sie über
  `dlna-playsingle://<Server>?sid=…&iid=<Objekt-ID>` mit den Metadaten aus `BrowseMetadata`.
- Anlegen: `CreateReference(ContainerID=MyFavorites, ObjectID=<Objekt>)`, entfernen: `DestroyObject`.
  Beides steht im Dienstprofil; Umbenennen gibt es nicht (der Titel kommt vom Ziel-Objekt).

Diese Funktionen sind bewusst UI-frei, damit auch die PixelPad-Brücke sie nutzen kann.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from .media import CD, PAGE_SIZE, container_uri
from .upnp import UpnpError
from .xmlsafe import parse

FAVORITES = "0/Favorites/MyFavorites"
TUNEIN_SEARCH = "0/RadioTime/Search"
MAX_FAVORITES = 500  # Sicherheitsgrenze beim seitenweisen Lesen

_FAV_ID = re.compile(r"0/Favorites/MyFavorites/[^/]+")
_DIDL = "{urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_UPNP = "{urn:schemas-upnp-org:metadata-1-0/upnp/}"
_SID = quote("urn:upnp-org:serviceId:ContentDirectory", safe="")


class FavoriteError(UpnpError):
    """Favorit ließ sich auf der Anlage nicht anlegen oder entfernen."""


@dataclass(frozen=True)
class AnlageFavorite:
    id: str  # Eintrag in MyFavorites, z. B. "0/Favorites/MyFavorites/315"
    ref_id: str | None  # Ziel-Objekt, z. B. "0/RadioTime/Search/s-s8235"
    title: str
    kind: str  # "station", "album", "artist", "track", "playlist" oder "other"
    container: bool
    art_url: str | None = None
    artist: str | None = None
    res: str | None = None  # nur manche Einträge (Künstler) tragen schon eine Abspieladresse


@dataclass(frozen=True)
class StationHit:
    """Treffer der TuneIn-Suche über die Anlage – ein Objekt, das ein Favorit werden kann."""
    id: str
    title: str
    art_url: str | None


def _text(el, tag: str) -> str | None:
    found = el.find(tag)
    return found.text.strip() if found is not None and found.text and found.text.strip() else None


def _kind(upnp_class: str, container: bool) -> str:
    if upnp_class.startswith("object.item.audioItem.audioBroadcast"):
        return "station"
    if upnp_class.startswith("object.container.album"):
        return "album"
    if upnp_class.startswith("object.container.person"):
        return "artist"
    if upnp_class.startswith("object.container.playlistContainer"):
        return "playlist"
    if upnp_class.startswith("object.item.audioItem"):
        return "track"
    return "other"


def _http(value: str | None) -> str | None:
    return value if value and value.lower().startswith(("http://", "https://")) else None


def parse_favorites(didl: str) -> list[AnlageFavorite]:
    if not didl:
        return []
    out: list[AnlageFavorite] = []
    for el in parse(didl):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "container"):
            continue
        container = tag == "container"
        res = el.find(f"{_DIDL}res")
        out.append(AnlageFavorite(
            id=el.get("id", ""),
            ref_id=el.get("refID") or None,
            title=_text(el, f"{_DC}title") or "",
            kind=_kind(_text(el, f"{_UPNP}class") or "", container),
            container=container,
            art_url=_http(_text(el, f"{_UPNP}albumArtURI")),
            artist=_text(el, f"{_UPNP}artist"),
            res=res.text.strip() if res is not None and res.text else None,
        ))
    return out


def _browse(server, object_id: str, start: int) -> tuple[str, int]:
    out = server.device.call(CD, "Browse", ObjectID=object_id, BrowseFlag="BrowseDirectChildren",
                             Filter="*", StartingIndex=start, RequestedCount=PAGE_SIZE, SortCriteria="")
    try:
        returned = int(out.get("NumberReturned") or 0)
    except ValueError:
        returned = 0
    return out.get("Result", ""), returned


def list_favorites(server) -> list[AnlageFavorite]:
    """Alle Favoriten in der Reihenfolge der Anlage (so zeigt sie auch die Teufel-App).

    Seitenweise nach NumberReturned – TotalMatches meldet die Anlage nicht überall verlässlich."""
    favs: list[AnlageFavorite] = []
    start = 0
    while len(favs) < MAX_FAVORITES:
        didl, returned = _browse(server, FAVORITES, start)
        page = parse_favorites(didl)
        favs.extend(f for f in page if _FAV_ID.fullmatch(f.id))
        if returned <= 0 or len(page) < PAGE_SIZE:
            break
        start += returned
    return favs


def playsingle_uri(server_udn: str, object_id: str) -> str:
    return (f"dlna-playsingle://{quote(server_udn, safe='')}?sid={_SID}"
            f"&iid={quote(object_id, safe='')}")


def play_uri(server_udn: str, fav: AnlageFavorite) -> str:
    """Abspieladresse wie die Teufel-App: Einzelobjekt direkt, Alben/Künstler als Wiedergabeliste."""
    if not fav.container:
        return playsingle_uri(server_udn, fav.id)
    if fav.res and fav.res.startswith("dlna-playcontainer://"):
        return fav.res
    return container_uri(server_udn, fav.ref_id or fav.id, 0)


def favorite_metadata(server, fav: AnlageFavorite) -> str:
    """DIDL für SetAVTransportURI – erst BrowseMetadata löst die Sender-Adresse auf."""
    return server.metadata(fav.id)


def find_by_ref(favs: list[AnlageFavorite], object_id: str) -> AnlageFavorite | None:
    return next((f for f in favs if f.ref_id == object_id or f.id == object_id), None)


def _check_target(object_id: str) -> None:
    if not object_id.startswith("0/") or object_id.startswith("0/Favorites/"):
        raise FavoriteError(f"Kein gültiges Ziel für einen Favoriten: {object_id}")


def _same_album(server, favs: list[AnlageFavorite], object_id: str) -> AnlageFavorite | None:
    """Ein Album hat zwei Pfade (…/Albums/X und …/Artists/A/A+X) – nach Interpret und Titel vergleichen."""
    albums = [f for f in favs if f.kind == "album"]
    if not albums or not object_id.startswith("0/My Music/"):
        return None
    found = parse_favorites(server.metadata(object_id))
    if not found or found[0].kind != "album":
        return None
    want = (found[0].title.casefold(), (found[0].artist or "").casefold())
    return next((f for f in albums if (f.title.casefold(), (f.artist or "").casefold()) == want), None)


def add_favorite(server, object_id: str) -> str:
    """Objekt der Anlage zu den Teufel-Favoriten; gibt die ID des Eintrags zurück.

    Idempotent: steht das Objekt schon in der Liste, wird nichts angelegt."""
    _check_target(object_id)
    favs = list_favorites(server)
    existing = find_by_ref(favs, object_id) or _same_album(server, favs, object_id)
    if existing is not None:
        return existing.id
    out = server.device.call(CD, "CreateReference", ContainerID=FAVORITES, ObjectID=object_id)
    new_id = out.get("NewID", "")
    if not _FAV_ID.fullmatch(new_id):
        raise FavoriteError(f"Anlage meldet unerwartete Favoriten-ID: {new_id!r}")
    return new_id


def remove_favorite(server, favorite_id: str) -> None:
    """Einen Eintrag aus MyFavorites entfernen – niemals ein Objekt außerhalb dieser Liste."""
    if not _FAV_ID.fullmatch(favorite_id):
        raise FavoriteError(f"Nur Einträge aus {FAVORITES} lassen sich entfernen: {favorite_id}")
    server.device.call(CD, "DestroyObject", ObjectID=favorite_id)


def search_stations(server, text: str, count: int = 30) -> list[StationHit]:
    """TuneIn-Sender über die Anlage suchen; nur Sender, keine Sendungen/Podcasts."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    out = server.device.call(CD, "Search", ContainerID=TUNEIN_SEARCH,
                             SearchCriteria=f'dc:title contains "{escaped}"', Filter="*",
                             StartingIndex=0, RequestedCount=count, SortCriteria="")
    return [StationHit(f.id, f.title, f.art_url) for f in parse_favorites(out.get("Result", ""))
            if f.kind == "station" and f.id.startswith(TUNEIN_SEARCH + "/") and _playable(f.title)]


def _playable(title: str) -> bool:
    """TuneIn liefert Platzhalter („Search results“) und abgeschaltete Sender („… - Not Supported“)."""
    t = title.strip().casefold()
    return bool(t) and t != "search results" and not t.endswith("not supported")


__all__ = [
    "FAVORITES", "TUNEIN_SEARCH", "AnlageFavorite", "FavoriteError", "StationHit",
    "add_favorite", "favorite_metadata", "find_by_ref", "list_favorites", "parse_favorites",
    "play_uri", "playsingle_uri", "remove_favorite", "search_stations",
]
