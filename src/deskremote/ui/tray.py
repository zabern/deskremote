"""Tray-Symbol mit Ausklappfenster (Linksklick) und Menü (Rechtsklick)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Signal
from PySide6.QtGui import QAction, QCursor, QGuiApplication
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..core.config import Config
from .popup import Popup
from .theme import STYLESHEET, tray_icon


class Tray(QSystemTrayIcon):
    action = Signal(str, tuple)
    open_settings = Signal()
    quit_requested = Signal()

    def __init__(self, popup: Popup, cfg: Config):
        super().__init__(tray_icon(False))
        self.popup = popup
        self.cfg = cfg
        self._online = False
        self.setToolTip("DeskRemote – verbinde …")
        self._menu = QMenu()
        self._menu.setStyleSheet(STYLESHEET)
        self._menu.aboutToShow.connect(self._build_menu)
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)

    def set_online(self, online: bool) -> None:
        self._online = online
        self.setIcon(tray_icon(online))
        self.setToolTip("DeskRemote" if online else "DeskRemote – Anlage nicht erreichbar")

    def notify(self, text: str) -> None:
        """Meldung im Ausklappfenster – bewusst keine Windows-Benachrichtigung."""
        self.popup.show_message(text)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            if self.popup.isVisible():
                self.popup.hide()
            elif not self.popup.recently_auto_hidden():
                self.show_popup()  # sonst war der Klick der, der das Fenster gerade geschlossen hat

    def show_popup(self) -> None:
        self.popup.adjustSize()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        size = self.popup.sizeHint()
        anchor = self.geometry().center() if self.geometry().isValid() else QCursor.pos()
        x = min(max(anchor.x() - size.width() // 2, area.left() + 8), area.right() - size.width() - 8)
        at_bottom = anchor.y() > area.center().y()
        y = area.bottom() - size.height() - 8 if at_bottom else area.top() + 8
        self.popup.anchor_bottom = area.bottom() - 8 if at_bottom else None
        self.popup.move(QPoint(x, y))
        self.popup.show()
        self.popup.raise_()
        self.popup.activateWindow()

    def _build_menu(self) -> None:
        self._menu.clear()
        for fav in self.cfg.favorites:
            act = QAction(f"▶ {fav.name.replace('&', '&&')}", self._menu)  # „&“ wörtlich, kein Kürzel
            act.setEnabled(self._online and bool(self.cfg.target_room))
            act.triggered.connect(lambda _=False, f=fav: self.action.emit("play_favorite", (self.cfg.target_room, f)))
            self._menu.addAction(act)
        self._menu.addSeparator()
        pause = QAction("Alle pausieren", self._menu)
        pause.setEnabled(self._online)
        pause.triggered.connect(lambda: self.action.emit("pause_all", ()))
        self._menu.addAction(pause)
        settings = QAction("Einstellungen", self._menu)
        settings.triggered.connect(self.open_settings.emit)
        self._menu.addAction(settings)
        self._menu.addSeparator()
        quit_act = QAction("Beenden", self._menu)
        quit_act.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(quit_act)
