"""Programmstart: Konfiguration, Hintergrund-Worker, Tray und Fenster verdrahten."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

import httpx
from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtWidgets import QApplication

from .core.anlage import Anlage
from .core.config import load_config, needs_logo_lookup, save_config
from .core.radio import RadioBrowser
from .log import get_logger, setup_logging
from .raumfeld.discovery import discover_host
from .raumfeld.host import RaumfeldHost
from .raumfeld.media import MediaServer, discover_media_servers
from .raumfeld.upnp import UpnpDevice
from .ui.couple import CoupleDialog
from .ui.covers import CoverLoader
from .ui.hotkeys import HotkeyManager, NoTarget, dispatch
from .ui.music import MusicWindow
from .ui.popup import Popup
from .ui.search import SearchWindow
from .ui.settings import SettingsWindow
from .ui.theme import app_icon
from .ui.tray import Tray
from .ui.worker import Worker


def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "DeskRemote" / "covers"


class _Bridge(QObject):
    """Rückkanal aus Hintergrund-Threads in den UI-Thread."""
    logos_done = Signal()


class App:
    def __init__(self, qapp: QApplication):
        self.qapp = qapp
        self.cfg = load_config()
        self.client = httpx.Client()
        self.worker = Worker(
            self.cfg,
            make_anlage=self._make_anlage,
            discover=lambda last: discover_host(last, self.client),
            save=save_config,
        )
        self.covers = CoverLoader(httpx.Client(), cache_dir(), online=lambda: self.cfg.online_covers)
        self._bridge = _Bridge()
        self._bridge.logos_done.connect(self._logos_done)
        self.popup = Popup(self.cfg, covers=self.covers)
        self.tray = Tray(self.popup, self.cfg)
        self._windows: dict[str, object] = {}
        self._rooms: list = []
        self.radio = RadioBrowser(self.client)
        self.hotkeys = HotkeyManager(self.on_hotkey)
        self.qapp.installNativeEventFilter(self.hotkeys)

        self.worker.state_changed.connect(self.popup.set_state)
        self.worker.state_changed.connect(lambda rooms: setattr(self, "_rooms", rooms))
        self.worker.online_changed.connect(self.popup.set_online)
        self.worker.online_changed.connect(self.tray.set_online)
        self.worker.error.connect(self.tray.notify)
        self.popup.action.connect(self.submit)
        self.tray.action.connect(self.submit)
        self.popup.target_changed.connect(lambda _u: save_config(self.cfg))
        self.popup.open_settings.connect(self.open_settings)
        self.tray.open_settings.connect(self.open_settings)
        self.popup.open_search.connect(self.open_search)
        self.popup.open_music.connect(self.open_music)
        self.popup.open_couple.connect(self.open_couple)
        self.tray.quit_requested.connect(self.qapp.quit)
        self.qapp.aboutToQuit.connect(self.shutdown)

    def _make_anlage(self, url: str) -> Anlage:
        return Anlage(RaumfeldHost(url, self.client), lambda loc: UpnpDevice.load(loc, self.client))

    def submit(self, action: str, args: tuple) -> None:
        self.worker.submit(action, *args)

    def play_on_target(self, fav) -> None:
        if not self.cfg.target_room:
            self.tray.notify("Kein Ziel-Raum gewählt – im Fenster einen Raum anklicken.")
            return
        self.submit("play_favorite", (self.cfg.target_room, fav))

    def _show(self, key: str, factory) -> None:
        win = self._windows.get(key)
        if win is None or not win.isVisible():
            win = factory()
            self._windows[key] = win
        self.popup.hide()
        win.show()
        win.raise_()
        win.activateWindow()

    def config_changed(self) -> None:
        self.popup.apply_config()
        self.register_hotkeys()

    def register_hotkeys(self) -> None:
        failed = self.hotkeys.register(self.cfg)
        if failed:
            names = ", ".join(self.cfg.hotkeys[n] or n for n in failed)
            self.tray.notify(f"Tastenkürzel schon belegt oder ungültig: {names}")

    def on_hotkey(self, name: str) -> None:
        try:
            todo = dispatch(name, self.cfg)
        except NoTarget:
            self.tray.notify("Kein Ziel-Raum gewählt – im Fenster einen Raum anklicken.")
            return
        if todo is not None:
            self.submit(*todo)

    def open_settings(self) -> None:
        def make():
            w = SettingsWindow(self.cfg, save=save_config)
            w.config_changed.connect(self.config_changed)
            w.closed.connect(self.register_hotkeys)
            return w
        self.hotkeys.unregister_all()  # sonst fangen die globalen Kürzel die Tasten beim Neubelegen ab
        self._show("settings", make)

    def open_search(self) -> None:
        def make():
            w = SearchWindow(self.radio, self.cfg, save=save_config)
            w.play.connect(self.play_on_target)
            w.favorites_changed.connect(self.popup.set_favorites)
            w.favorites_changed.connect(self._sync_settings)
            return w
        self._show("search", make)

    def _find_media_servers(self) -> list:
        host = self.cfg.last_host or discover_host(None, self.client)
        known = list(RaumfeldHost(host, self.client).list_devices().values()) if host else []
        servers = []
        for info in discover_media_servers(self.client, known):
            try:
                servers.append(MediaServer(info.udn, info.name, UpnpDevice.load(info.location, self.client),
                                           raumfeld=info.trusted))
            except Exception:  # noqa: BLE001 – unerreichbare Quelle auslassen
                continue
        return servers

    def play_container(self, uri: str, metadata: str) -> None:
        if not self.cfg.target_room:
            self.tray.notify("Kein Ziel-Raum gewählt – im Fenster einen Raum anklicken.")
            return
        self.submit("play_container", (self.cfg.target_room, uri, metadata))

    def play_container_shuffled(self, uri: str, metadata: str) -> None:
        if self.cfg.target_room:
            self.submit("play_container", (self.cfg.target_room, uri, metadata, True))

    def open_music(self) -> None:
        def make():
            w = MusicWindow([], self.covers, self.cfg, save=save_config)
            w.play.connect(self.play_container)
            w.play_shuffled.connect(self.play_container_shuffled)
            w.favorites_changed.connect(self.popup.set_favorites)
            w.favorites_changed.connect(self._sync_settings)
            w.discover(self._find_media_servers)
            return w
        self._show("music", make)

    def _lookup_logos(self) -> None:
        """Logos für Favoriten nachschlagen, die noch keins haben (einmalig, im Hintergrund)."""
        todo = [f for f in self.cfg.favorites if needs_logo_lookup(f)]
        if not todo:
            return

        def work():
            for fav in todo:
                try:
                    fav.logo = self.radio.logo_for(fav.url) or ""
                except Exception:  # noqa: BLE001
                    continue
            self._bridge.logos_done.emit()

        threading.Thread(target=work, name="logo-lookup", daemon=True).start()

    def _logos_done(self) -> None:
        save_config(self.cfg)
        self.popup.set_favorites()

    def _sync_settings(self) -> None:
        win = self._windows.get("settings")
        if win is not None and win.isVisible():
            win.sync_new_favorites()

    def open_couple(self) -> None:
        def make():
            w = CoupleDialog(self._rooms, self.cfg.target_room)
            w.action.connect(self.submit)
            self.worker.state_changed.connect(w.set_rooms)  # zeigt live, was die Anlage bestätigt
            return w
        self._show("couple", make)

    def start(self) -> None:
        self.tray.show()
        self.register_hotkeys()
        self.worker.start()
        self._lookup_logos()

    def shutdown(self) -> None:
        self.hotkeys.unregister_all()
        self.worker.stop()
        save_config(self.cfg)
        self.client.close()


def main() -> int:
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("DeskRemote")
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(app_icon())
    lock = QLockFile(os.path.join(tempfile.gettempdir(), "deskremote.lock"))
    if not lock.tryLock(100):
        return 0  # läuft schon
    setup_logging()
    from . import __version__
    get_logger().info("DeskRemote v%s gestartet", __version__)
    app = App(qapp)
    app.start()
    return qapp.exec()
