"""Einstellungen: Favoriten verwalten und Tastenkürzel belegen."""

from __future__ import annotations

from dataclasses import replace

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QInputDialog, QKeySequenceEdit, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from ..core.config import Config, Favorite
from .theme import app_icon, checkbox_style, PlainLabel, STYLESHEET

HOTKEY_LABELS = {
    "volume_up": "Lauter (Ziel-Raum)",
    "volume_down": "Leiser (Ziel-Raum)",
    "play_pause": "Play/Pause (Ziel-Raum)",
    "favorite_1": "Favorit 1",
    "favorite_2": "Favorit 2",
    "favorite_3": "Favorit 3",
    "standby": "Standby (Ziel-Raum)",
}


def valid_url(url: str) -> bool:
    return url.strip().lower().startswith(("http://", "https://"))

KIND_LABELS = {"album": "Album", "track": "Titel", "artist": "Künstler · Zufall"}  # Sender zeigen ihre Adresse


class SettingsWindow(QWidget):
    config_changed = Signal()
    closed = Signal()

    def __init__(self, cfg: Config, save: Callable[[Config], None]):
        super().__init__(None, Qt.Window)
        self.cfg, self.save = cfg, save
        self._favs = [replace(f) for f in cfg.favorites]  # Kopien mit allen Feldern (Musik!)
        self._seen_urls = {(f.kind, f.url) for f in cfg.favorites}  # was dieses Fenster schon kennt (auch Gelöschtes)
        self.setWindowTitle("Einstellungen")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLESHEET + checkbox_style())  # weißer Haken wie beim Koppeln
        self.resize(560, 640)

        root = QVBoxLayout(self)
        name_row = QHBoxLayout()
        name_row.addWidget(PlainLabel("Überschrift im Fenster:"))
        self.title_edit = QLineEdit(cfg.title)
        self.title_edit.setObjectName("title")
        self.title_edit.setMaxLength(40)
        name_row.addWidget(self.title_edit, 1)
        root.addLayout(name_row)
        self.online_covers = QCheckBox("Fehlende Cover online suchen (MusicBrainz – sendet nur Interpret und Album)")
        self.online_covers.setObjectName("online_covers")
        self.online_covers.setChecked(cfg.online_covers)
        root.addWidget(self.online_covers)

        head = PlainLabel("Favoriten")
        head.setObjectName("heading")
        root.addWidget(head)
        self.list = QListWidget()
        self.list.setObjectName("favorites")
        self.list.setMinimumHeight(240)
        root.addWidget(self.list, 3)

        row = QHBoxLayout()
        for text, name, slot in (("Hoch", "up", lambda: self._move(-1)), ("Runter", "down", lambda: self._move(1)),
                                 ("Umbenennen…", "rename", self._ask_rename), ("Löschen", "delete", self._delete),
                                 ("Adresse von Hand…", "manual", self._ask_manual)):
            b = QPushButton(text)
            b.setObjectName(name)
            b.clicked.connect(slot)
            row.addWidget(b)
        root.addLayout(row)

        head2 = PlainLabel("Tastenkürzel")
        head2.setObjectName("heading")
        root.addWidget(head2)
        hint = PlainLabel("Funktionieren überall, auch wenn das Fenster zu ist. Alles geht auch per Maus.")
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        root.addWidget(hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self.hotkey_edits: dict[str, QKeySequenceEdit] = {}
        for i, (key, label) in enumerate(HOTKEY_LABELS.items()):
            edit = QKeySequenceEdit(QKeySequence(cfg.hotkeys.get(key, "")))
            edit.setObjectName(f"hotkey:{key}")
            self.hotkey_edits[key] = edit
            row, col = divmod(i, 2)
            grid.addWidget(PlainLabel(label), row, col * 2)
            grid.addWidget(edit, row, col * 2 + 1)
        root.addLayout(grid)

        bottom = QHBoxLayout()
        bottom.addStretch()
        cancel = QPushButton("Abbrechen")
        cancel.setObjectName("cancel")
        cancel.clicked.connect(self.close)
        save_btn = QPushButton("Speichern")
        save_btn.setObjectName("save")
        save_btn.clicked.connect(self._save)
        bottom.addWidget(cancel)
        bottom.addWidget(save_btn)
        root.addLayout(bottom)
        self._refresh_list()

    # --- öffentlich (auch für Tests) ------------------------------------
    def add_favorite(self, name: str, url: str) -> bool:
        if not name.strip() or not valid_url(url):
            return False
        self._favs.append(Favorite(name.strip(), url.strip()))
        self._refresh_list(len(self._favs) - 1)
        return True

    def rename_current(self, name: str) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._favs) and name.strip():
            self._favs[row].name = name.strip()
            self._refresh_list(row)

    def sync_new_favorites(self) -> None:
        """Favoriten, die woanders (Sendersuche ☆) dazukamen, übernehmen – ohne eigene Änderungen zu verwerfen."""
        added = [f for f in self.cfg.favorites if (f.kind, f.url) not in self._seen_urls]
        if not added:
            return
        row = self.list.currentRow()
        for f in added:
            self._favs.append(replace(f))
            self._seen_urls.add((f.kind, f.url))
        self._refresh_list(row if row >= 0 else None)

    def set_hotkey(self, key: str, seq: str) -> None:
        self.hotkey_edits[key].setKeySequence(QKeySequence(seq))

    # --- intern ---------------------------------------------------------
    def _refresh_list(self, select: int | None = None) -> None:
        self.list.clear()
        for f in self._favs:
            self.list.addItem(f"{f.name}    —    {KIND_LABELS.get(f.kind, f.url)}")
        if select is not None and self._favs:
            self.list.setCurrentRow(max(0, min(select, len(self._favs) - 1)))

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        new = row + delta
        if 0 <= row < len(self._favs) and 0 <= new < len(self._favs):
            self._favs[row], self._favs[new] = self._favs[new], self._favs[row]
            self._refresh_list(new)

    def _delete(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._favs):
            del self._favs[row]
            self._refresh_list(row)

    def _ask_rename(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self._favs):
            return
        name, ok = QInputDialog.getText(self, "Umbenennen", "Neuer Name:", text=self._favs[row].name)
        if ok:
            self.rename_current(name)

    def _ask_manual(self) -> None:
        name, ok = QInputDialog.getText(self, "Sender von Hand", "Name:")
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(self, "Sender von Hand", "Stream-Adresse (http/https):")
        if ok and not self.add_favorite(name, url):
            QMessageBox.warning(self, "Sender von Hand", "Die Adresse muss mit http:// oder https:// beginnen.")

    def _save(self) -> None:
        self.cfg.title = self.title_edit.text().strip() or "DeskRemote"
        self.cfg.online_covers = self.online_covers.isChecked()
        self.cfg.favorites = [replace(f) for f in self._favs]
        for key, edit in self.hotkey_edits.items():
            self.cfg.hotkeys[key] = edit.keySequence().toString(QKeySequence.PortableText)
        self.save(self.cfg)
        self.config_changed.emit()
        self.close()

    def closeEvent(self, event):
        super().closeEvent(event)
        self.closed.emit()
