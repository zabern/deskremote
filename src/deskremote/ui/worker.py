"""Hintergrund-Arbeiter: Verbindung halten, Änderungen beobachten, Aktionen ausführen.

Die Oberfläche ruft nur `submit()` auf und hört auf Signale – sie wartet nie auf das Netz.

Fehlerklassen:
- Transportfehler (keine Verbindung) → offline, Neuaufbau nach `retry_interval`.
- Ablehnung durch die Anlage (HTTP-Status, UPnP-Fehler, kaputte Antwort) → Meldung, bleibt online.
- Long-Poll-Timeout → „keine Änderung“, weiter beobachten.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

import httpx
from PySide6.QtCore import QObject, Signal

from ..i18n import tr
from ..core.anlage import PlayError
from ..core.config import Config
from ..log import get_logger, short
from ..raumfeld.upnp import UpnpError
from ..raumfeld.xmlsafe import XmlError

ALLOWED_ACTIONS = {
    "play_stream", "play_favorite", "toggle_play", "set_volume", "change_volume",
    "standby", "wake", "couple", "uncouple", "pause_all",
    "standby_group", "change_group_volume",
    "play_container", "next_track", "previous_track", "set_play_mode",
}
# Teufel-Favoriten: liefern die neue Liste zurück (Signal favorites_loaded)
FAVORITE_ACTIONS = {"load_favorites": "favorites", "add_favorite": "add_favorite",
                    "remove_favorite": "remove_favorite", "reorder_favorites": "reorder_favorites"}
FAVORITES_INTERVAL = 60.0  # so oft nachsehen, ob jemand in der Teufel-App etwas geändert hat


def _log_args(act: str, args: tuple) -> str:
    """Fürs Protokoll: bei Alben nur Raum + Album-ID, nie den Metadaten-DIDL."""
    if act == "play_container" and len(args) >= 2:
        from urllib.parse import parse_qs, urlsplit
        cid = parse_qs(urlsplit(str(args[1])).query).get("cid", ["?"])[0]
        fii = parse_qs(urlsplit(str(args[1])).query).get("fii", ["0"])[0]
        return short((args[0], cid, f"ab Titel {int(fii) + 1 if fii.isdigit() else fii}"))
    if act == "play_favorite" and len(args) >= 2 and hasattr(args[1], "kind"):
        return short((args[0], args[1].kind, args[1].name))  # nie die DIDL-Metadaten
    return short(args)
VOLUME_SETTLE = 0.15
NO_UPDATE_ID_PAUSE = 1.0
OFFLINE_TEXT = "Anlage nicht erreichbar"
_REFRESH = ("__refresh__", ())
_STOP = ("__stop__", ())


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return tr("Die Anlage hat die Aktion abgelehnt (HTTP {status})", status=exc.response.status_code)
    if isinstance(exc, XmlError):
        return tr("Die Anlage hat eine unverständliche Antwort geschickt")
    return str(exc)


class Worker(QObject):
    state_changed = Signal(list)
    online_changed = Signal(bool)
    error = Signal(str)
    favorites_loaded = Signal(list)  # Teufel-Favoriten, frisch von der Anlage
    _save_requested = Signal()  # wird im UI-Thread ausgeführt (Worker-Objekt lebt dort)

    def __init__(self, cfg: Config, make_anlage: Callable[[str], Any],
                 discover: Callable[[str | None], str | None], save: Callable[[Config], None],
                 poll_interval: float = 5.0, retry_interval: float = 5.0):
        super().__init__()
        self.cfg = cfg
        self.make_anlage = make_anlage
        self.discover = discover
        self.save = save
        self.poll_interval = poll_interval
        self.retry_interval = retry_interval
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._anlage = None
        self._online: bool | None = None
        self._threads: list[threading.Thread] = []
        self._favorites_at = float("-inf")
        self._save_requested.connect(lambda: self.save(self.cfg))

    # --- öffentlich -----------------------------------------------------
    def start(self) -> None:
        self._spawn(self._run, "raumfeld-worker")

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(_STOP)
        deadline = time.monotonic() + 2.0
        for t in self._threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))

    @property
    def anlage(self):
        """Die verbundene Anlage oder None (nur lesend nutzen, z. B. für die Sendersuche)."""
        return self._anlage

    def submit(self, action: str, *args: Any) -> None:
        self._queue.put((action, args))

    # --- intern ---------------------------------------------------------
    def _spawn(self, target, name: str, *args) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        t = threading.Thread(target=target, args=args, name=name, daemon=True)
        self._threads.append(t)
        t.start()

    def _set_online(self, online: bool) -> None:
        if online != self._online:
            get_logger().info("Anlage %s", "erreichbar" if online else "NICHT erreichbar")
            self._online = online
            self.online_changed.emit(online)

    def _connect(self) -> bool:
        url = self.discover(self.cfg.last_host)
        if not url:
            return False
        anlage = self.make_anlage(url)
        self.state_changed.emit(anlage.refresh())  # erst wenn das klappt, gilt die Anlage als da
        self._anlage = anlage
        if url != self.cfg.last_host:
            self.cfg.last_host = url
            self._save_requested.emit()
        self._spawn(self._long_poll, "raumfeld-poll", anlage)
        self._set_online(True)
        self._favorites_at = float("-inf")  # gleich nach dem Verbinden die Teufel-Favoriten holen
        return True

    def _favorites(self, action: str, args: tuple) -> None:
        """Teufel-Favoriten lesen/ändern. Lesen im Hintergrund bleibt stumm, Ändern meldet Fehler."""
        self._favorites_at = time.monotonic()
        loud = action != "load_favorites"
        if loud:
            get_logger().info("Aktion %s %s", action, short(args))
        try:
            favs = getattr(self._anlage, FAVORITE_ACTIONS[action])(*args)
        except (UpnpError, httpx.HTTPStatusError, XmlError) as exc:
            get_logger().warning("Teufel-Favoriten (%s) fehlgeschlagen: %s", action, short(exc))
            if loud:
                self.error.emit(_describe(exc))
            return
        self.favorites_loaded.emit(list(favs))

    def _drop(self) -> None:
        self._anlage = None
        self._set_online(False)

    def _long_poll(self, anlage) -> None:
        update_id = None
        while not self._stop.is_set() and self._anlage is anlage:
            try:
                _, new_id = anlage.host.get_zones(update_id)
            except httpx.ReadTimeout:
                continue  # Host hat nichts Neues – weiter beobachten
            except Exception:  # noqa: BLE001 – alles andere: Verbindung neu aufbauen
                self._queue.put(("__lost__", (anlage,)))
                return
            if update_id is not None and new_id != update_id:
                self._queue.put(_REFRESH)
            if not new_id:
                self._stop.wait(NO_UPDATE_ID_PAUSE)  # ohne updateID kein Long-Poll – nicht im Kreis jagen
            update_id = new_id or update_id

    def _next(self, timeout: float):
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return _REFRESH

    def _coalesce_volume(self, first: tuple) -> list[tuple]:
        """Sammelt schnell aufeinanderfolgende set_volume-Aufrufe; je Raum gewinnt der letzte Wert."""
        time.sleep(VOLUME_SETTLE)
        latest: dict[str, tuple] = {first[1][0]: first}
        others: list[tuple] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item[0] == "set_volume":
                latest[item[1][0]] = item
            else:
                others.append(item)
        for item in others:
            self._queue.put(item)
        return list(latest.values())

    def _run_offline_step(self) -> bool:
        """Versucht zu verbinden; sonst warten. Liefert False, wenn gestoppt werden soll."""
        try:
            connected = self._connect()
        except Exception:  # noqa: BLE001
            connected = False
        if connected:
            return True
        self._anlage = None
        self._set_online(False)
        action, _args = self._next(self.retry_interval)
        if action == "__stop__":
            return False
        if action in ALLOWED_ACTIONS or action in ("add_favorite", "remove_favorite", "reorder_favorites"):
            self.error.emit(tr(OFFLINE_TEXT))  # nie still verlieren – sonst taucht ein „Gelöschtes“ wieder auf
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._anlage is None:
                if not self._run_offline_step():
                    return
                continue

            action, args = self._next(self.poll_interval)
            if action == "__stop__":
                return
            if action == "__lost__":
                if args and args[0] is self._anlage:
                    self._drop()
                continue
            if action in FAVORITE_ACTIONS or (
                    action == "__refresh__" and time.monotonic() - self._favorites_at >= FAVORITES_INTERVAL):
                try:
                    self._favorites(action if action in FAVORITE_ACTIONS else "load_favorites", args
                                    if action in FAVORITE_ACTIONS else ())
                except httpx.TransportError:  # Verbindung weg: neu verbinden
                    self._drop()
                    continue
                except Exception as exc:  # noqa: BLE001 – nie den Worker an den Favoriten sterben lassen
                    get_logger().warning("Teufel-Favoriten: %s", short(exc))
                if action in FAVORITE_ACTIONS:
                    continue
            batch = [(action, args)]
            if action == "set_volume":
                batch = self._coalesce_volume((action, args))
            try:
                for act, a in batch:
                    if act == "__refresh__":
                        continue
                    if act not in ALLOWED_ACTIONS:
                        self.error.emit(tr("Unbekannte Aktion: {action}", action=act))
                        continue
                    get_logger().info("Aktion %s %s", act, _log_args(act, a))
                    try:
                        getattr(self._anlage, act)(*a)
                    except (PlayError, UpnpError, httpx.HTTPStatusError, XmlError) as exc:
                        get_logger().warning("Aktion %s fehlgeschlagen: %s", act, short(exc))
                        self.error.emit(_describe(exc))
                self.state_changed.emit(self._anlage.refresh())
            except httpx.HTTPStatusError as exc:
                self.error.emit(_describe(exc))
            except Exception:  # noqa: BLE001 – Transportfehler o. Ä.: neu verbinden, Thread lebt weiter
                self._drop()
