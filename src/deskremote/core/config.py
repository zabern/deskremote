"""Einstellungen und Favoriten, gespeichert als JSON unter %APPDATA%\\DeskRemote."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


MUSIC_KINDS = ("album", "track", "artist")
MAX_META = 16_000  # DIDL eines Albums ist ~1–2 KB; mehr ist kein echter Eintrag
MAX_COUNT = 100_000  # Titel je Künstler – mehr ist kein echter Eintrag
_PLAYCONTAINER = "dlna-playcontainer://"


@dataclass
class Favorite:
    name: str
    url: str  # Sender: Stream-Adresse; Musik: dlna-playcontainer://…
    logo: str | None = None  # None = noch nicht gesucht, "" = gesucht, keins gefunden
    kind: str = "stream"  # "stream" oder eine der MUSIC_KINDS
    meta: str = ""  # Musik: DIDL-Metadaten für SetAVTransportURI
    cover: str | None = None  # Musik: Cover-Adresse (Raumfeld-Host)
    count: int = 0  # Künstler: Titelanzahl für den zufälligen Einstieg

    @property
    def is_music(self) -> bool:
        return self.kind != "stream"


def needs_logo_lookup(fav: Favorite) -> bool:
    """Senderlogo nachschlagen? Nur für Sender, die noch nie gesucht wurden."""
    return fav.logo is None and not fav.is_music


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


def default_config() -> Config:
    return Config()


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
    )


def save_config(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomar: nie eine halb geschriebene Datei
