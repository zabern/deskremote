"""Einstellungen und Favoriten, gespeichert als JSON unter %APPDATA%\\DeskRemote."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ..i18n import LANGUAGES, system_language


MUSIC_KINDS = ("album", "track", "artist")
ANLAGE = "anlage"  # Favorit der Teufel-App (liegt auf der Anlage, nicht in dieser Datei)
ANLAGE_SUBS = ("station", "album", "artist", "track", "playlist", "other")
HOTKEY_SLOTS = 3
MAX_META = 16_000  # DIDL eines Albums ist ~1–2 KB; mehr ist kein echter Eintrag
MAX_COUNT = 100_000  # Titel je Künstler – mehr ist kein echter Eintrag
_PLAYCONTAINER = "dlna-playcontainer://"
_PLAYSINGLE = "dlna-playsingle://"
_ANLAGE_ENTRY = re.compile(r"0/Favorites/MyFavorites/[^/]+")


@dataclass
class Favorite:
    name: str
    url: str  # Sender: Stream-Adresse; Musik: dlna-playcontainer://…
    logo: str | None = None  # None = noch nicht gesucht, "" = gesucht, keins gefunden
    kind: str = "stream"  # "stream" oder eine der MUSIC_KINDS
    meta: str = ""  # Musik: DIDL-Metadaten für SetAVTransportURI
    cover: str | None = None  # Musik: Cover-Adresse (Raumfeld-Host)
    count: int = 0  # Künstler: Titelanzahl für den zufälligen Einstieg
    ref: str = ""  # Teufel-Favorit: Eintrag auf der Anlage (0/Favorites/MyFavorites/…), wechselt beim Neuanlegen
    target: str = ""  # Teufel-Favorit: Ziel-Objekt (z. B. 0/RadioTime/Search/s-s8235) – bleibt gleich
    sub: str = ""  # Teufel-Favorit: "station", "album", "artist", …

    @property
    def is_music(self) -> bool:
        if self.kind == ANLAGE:
            return self.sub != "station"
        return self.kind != "stream"

    @property
    def on_anlage(self) -> bool:
        return self.kind == ANLAGE

    @property
    def key(self) -> str:
        """Bleibende Kennung (für Kurzwahl-Belegung und Sterne): Ziel-Objekt bzw. Adresse."""
        return self.target if self.kind == ANLAGE and self.target else self.url


def needs_logo_lookup(fav: Favorite) -> bool:
    """Senderlogo nachschlagen? Nur für eigene Sender, die noch nie gesucht wurden."""
    return fav.logo is None and fav.kind == "stream"


def _http_or_none(value: object, what: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{what} ungültig")
    if value and not value.lower().startswith(("http://", "https://")):
        return None
    if value:
        try:
            if not urlsplit(value).hostname:
                return None
        except ValueError:  # z. B. "http://[::1" – nie bis in die Oberfläche durchreichen
            return None
    return value


def _favorite_from_dict(f: object) -> Favorite:
    if not (isinstance(f, dict) and isinstance(f.get("name"), str) and isinstance(f.get("url"), str)):
        raise TypeError("Favorit ungültig")
    kind = f.get("kind", "stream")
    if kind not in ("stream",) + MUSIC_KINDS:
        raise ValueError("Favoriten-Art unbekannt")
    url = f["url"]
    logo = _http_or_none(f.get("logo"), "Logo")
    if kind == "stream":
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("Favoriten-Adresse muss http(s) sein")
        return Favorite(f["name"], url, logo)
    if not url.startswith(_PLAYCONTAINER):
        raise ValueError("Musik-Favorit braucht eine dlna-playcontainer-Adresse")
    meta = f.get("meta", "")
    count = f.get("count", 0)
    if not isinstance(meta, str) or len(meta) > MAX_META:
        raise ValueError("Metadaten ungültig")
    if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= MAX_COUNT:
        raise ValueError("Titelanzahl ungültig")
    return Favorite(f["name"], url, logo, kind, meta, _http_or_none(f.get("cover"), "Cover"), count)


def _anlage_favorite_from_dict(f: object) -> Favorite | None:
    """Zwischengespeicherter Teufel-Favorit; Ungültiges wird verworfen, nie die ganze Config."""
    if not isinstance(f, dict) or f.get("kind") != ANLAGE:
        return None
    name, url, ref, target, sub = (f.get(k) for k in ("name", "url", "ref", "target", "sub"))
    if not all(isinstance(v, str) for v in (name, url, ref, target, sub)):
        return None
    if not url.startswith((_PLAYSINGLE, _PLAYCONTAINER)) or not _ANLAGE_ENTRY.fullmatch(ref):
        return None
    if not target.startswith("0/") or target.startswith("0/Favorites/") or sub not in ANLAGE_SUBS:
        return None
    try:
        logo = _http_or_none(f.get("logo"), "Logo")
        cover = _http_or_none(f.get("cover"), "Cover")
    except TypeError:
        return None
    return Favorite(name[:200], url, logo if logo is not None else "", ANLAGE, "", cover, 0, ref, target, sub)


def _anlage_favorites(value: object) -> list[Favorite] | None:
    if not isinstance(value, list):
        return None
    return [fav for f in value if (fav := _anlage_favorite_from_dict(f)) is not None]


def _hotkey_favorites(value: object) -> list[str]:
    slots = [v if isinstance(v, str) else "" for v in value] if isinstance(value, list) else []
    return (slots + [""] * HOTKEY_SLOTS)[:HOTKEY_SLOTS]


def _default_favorites() -> list[Favorite]:
    return [
        Favorite("FM4", "https://orf-live.ors-shoutcast.at/fm4-q2a"),
        Favorite("Ö1", "http://orf-live.ors-shoutcast.at/oe1-q2a"),
        Favorite("Kronehit", "http://onair-ha1.krone.at/kronehit1058.mp3"),
    ]


def _default_hotkeys() -> dict[str, str]:
    return {
        "volume_up": "Ctrl+Alt+Up",
        "volume_down": "Ctrl+Alt+Down",
        "play_pause": "Ctrl+Alt+Space",
        "favorite_1": "Ctrl+Alt+1",
        "favorite_2": "Ctrl+Alt+2",
        "favorite_3": "Ctrl+Alt+3",
        "standby": "Ctrl+Alt+0",
    }


@dataclass
class Config:
    favorites: list[Favorite] = field(default_factory=_default_favorites)
    hotkeys: dict[str, str] = field(default_factory=_default_hotkeys)
    target_room: str | None = None
    last_host: str | None = None
    search_country: str = "AT"
    volume_step: int = 5
    title: str = "DeskRemote"
    online_covers: bool = True  # fehlende Cover bei MusicBrainz suchen
    music_source: str | None = None  # zuletzt gewählte Musikquelle (UDN)
    language: str = "de"  # Oberflächensprache, siehe i18n.LANGUAGES
    # Teufel-Favoriten: None = noch nie von der Anlage gelesen → bis dahin gilt die eigene Liste oben.
    # Eigener Schlüssel, damit ältere DeskRemote-Versionen die Datei weiter lesen können.
    anlage_favorites: list[Favorite] | None = None
    hotkey_favorites: list[str] = field(default_factory=lambda: [""] * HOTKEY_SLOTS)  # Ziel je Kürzel, "" = Platz

    def active_favorites(self) -> list[Favorite]:
        """Was Fenster, Tray und Kürzel zeigen: die Teufel-Favoriten, sobald sie einmal gelesen wurden."""
        return self.anlage_favorites if self.anlage_favorites is not None else self.favorites

    def hotkey_favorite(self, slot: int) -> Favorite | None:
        """Favorit für Strg+Alt+1–3: festgelegtes Ziel, sonst der Platz in der Liste."""
        favs = self.active_favorites()
        key = self.hotkey_favorites[slot] if 0 <= slot < len(self.hotkey_favorites) else ""
        if key:
            return next((f for f in favs if f.key == key), None)
        return favs[slot] if 0 <= slot < len(favs) else None


def default_config() -> Config:
    """Erster Start (keine Config): Sprache nach Windows. Bestehende Configs ohne Feld bleiben Deutsch."""
    return Config(language=system_language())


def config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "DeskRemote" / "config.json"


def _take_over_old_config(new: Path) -> None:
    """Früherer Name „DeskRemote“: vorhandene Einstellungen einmalig übernehmen (alte Datei bleibt)."""
    old = new.parent.parent / "RaumfeldDesk" / "config.json"
    if new.exists() or not old.exists():
        return
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        new.write_bytes(old.read_bytes())
    except OSError:
        pass


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = config_path()
        _take_over_old_config(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _from_dict(data)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return default_config()


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    raise TypeError("Ja/Nein erwartet")


def _opt_str(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError("Text erwartet")


def _from_dict(data: object) -> Config:
    """Baut die Config streng typgeprüft; jede Abweichung führt zu den Standardwerten."""
    if not isinstance(data, dict):
        raise TypeError("Objekt erwartet")
    favorites = [_favorite_from_dict(f) for f in data.get("favorites", [])]
    hotkeys = _default_hotkeys()
    raw_hotkeys = data.get("hotkeys", {})
    if not isinstance(raw_hotkeys, dict) or not all(isinstance(v, str) for v in raw_hotkeys.values()):
        raise TypeError("Tastenkürzel ungültig")
    hotkeys.update({k: v for k, v in raw_hotkeys.items() if k in hotkeys})
    language = data.get("language", "de")
    if language not in LANGUAGES:
        raise ValueError("Sprache unbekannt")
    step = data.get("volume_step", 5)
    if not isinstance(step, int) or isinstance(step, bool) or not 1 <= step <= 25:
        raise ValueError("Lautstärkeschritt ungültig")
    return Config(
        favorites=favorites,
        hotkeys=hotkeys,
        target_room=_opt_str(data.get("target_room")),
        last_host=_opt_str(data.get("last_host")),
        search_country=_opt_str(data.get("search_country", "AT")) or "",
        volume_step=step,
        title=(_opt_str(data.get("title", "DeskRemote")) or "DeskRemote")[:40],
        online_covers=_bool(data.get("online_covers", True)),
        music_source=_opt_str(data.get("music_source")),
        language=language,
        anlage_favorites=_anlage_favorites(data.get("anlage_favorites")),
        hotkey_favorites=_hotkey_favorites(data.get("hotkey_favorites")),
    )


def save_config(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomar: nie eine halb geschriebene Datei


def _norm(text: str) -> str:
    t = text.casefold()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    return "".join(ch for ch in t if ch.isalnum())


def _same_favorite(old: Favorite, fav: Favorite) -> bool:
    """Alter eigener Favorit ↔ Teufel-Favorit: gleiche Adresse oder eindeutig gleicher Name."""
    if old.url == fav.url:
        return True
    a, b = _norm(old.name), _norm(fav.name)
    return len(a) >= 3 and len(b) >= 3 and (a == b or a in b or b in a)


def take_anlage_favorites(cfg: Config, favs: list[Favorite]) -> tuple[bool, list[str]]:
    """Frische Teufel-Favoriten übernehmen.

    Beim allerersten Mal werden die Kürzel Strg+Alt+1–3 aus der bisherigen Liste vorbelegt, sofern
    ein Teufel-Favorit eindeutig passt – sonst würden sie still auf andere Sender springen.
    Liefert (geändert?, Namen alter Kürzel-Favoriten ohne eindeutigen Treffer)."""
    first = cfg.anlage_favorites is None
    unmatched: list[str] = []
    if first and not any(cfg.hotkey_favorites):
        for slot, old in enumerate(cfg.favorites[:HOTKEY_SLOTS]):
            hits = [f for f in favs if _same_favorite(old, f)]
            if len(hits) == 1:
                cfg.hotkey_favorites[slot] = hits[0].key
            else:  # alte Adresse behalten: Kürzel spielt nichts Falsches, Einstellungen zeigen „nicht mehr da“
                cfg.hotkey_favorites[slot] = old.key
                unmatched.append(old.name)
    changed = first or favs != cfg.anlage_favorites
    cfg.anlage_favorites = list(favs)
    return changed, unmatched
