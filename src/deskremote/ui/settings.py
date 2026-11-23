"""Einstellungen: Favoriten verwalten und Tastenkürzel belegen."""

from __future__ import annotations

from dataclasses import replace

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QKeySequenceEdit, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..core.config import HOTKEY_SLOTS, Config, Favorite
from ..i18n import LANGUAGES, language, set_language, tr
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
ANLAGE_LABELS = {"station": "Teufel-Favorit · Sender", "album": "Teufel-Favorit · Album",
                 "artist": "Teufel-Favorit · Künstler", "track": "Teufel-Favorit · Titel",
                 "playlist": "Teufel-Favorit · Wiedergabeliste", "other": "Teufel-Favorit"}


class SettingsWindow(QWidget):
    config_changed = Signal()
    closed = Signal()
    restart_requested = Signal()
    remove_on_anlage = Signal(list)  # Ziel-Objekte, die aus den Teufel-Favoriten entfernt werden sollen
    reorder_on_anlage = Signal(list)  # gewünschte Reihenfolge der Teufel-Favoriten (Ziel-Objekte)

    def __init__(self, cfg: Config, save: Callable[[Config], None]):
        super().__init__(None, Qt.Window)
        self.cfg, self.save = cfg, save
        self._anlage = cfg.anlage_favorites is not None  # Teufel-Favoriten statt eigener Liste
        self._removed: set[str] = set()  # zum Entfernen vorgemerkte Teufel-Favoriten (Ziel-Objekt)
        self._favs = [replace(f) for f in cfg.active_favorites()]  # Kopien mit allen Feldern (Musik!)
        self._seen_urls = {(f.kind, f.url) for f in cfg.favorites}  # was dieses Fenster schon kennt (auch Gelöschtes)
        self.setWindowTitle(tr("Einstellungen"))
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLESHEET + checkbox_style())  # weißer Haken wie beim Koppeln
        self.resize(560, 640)

        root = QVBoxLayout(self)
        name_row = QHBoxLayout()
        name_row.addWidget(PlainLabel(tr("Überschrift im Fenster:")))
        self.title_edit = QLineEdit(cfg.title)
        self.title_edit.setObjectName("title")
        self.title_edit.setMaxLength(40)
        name_row.addWidget(self.title_edit, 1)
        name_row.addSpacing(12)
        name_row.addWidget(PlainLabel(tr("Sprache:")))
        self.language = QComboBox()
        self.language.setObjectName("language")
        for code, label in LANGUAGES.items():
            self.language.addItem(label, code)
        self.language.setCurrentIndex(max(0, self.language.findData(cfg.language)))
        name_row.addWidget(self.language)
        root.addLayout(name_row)
        self.online_covers = QCheckBox(tr("Fehlende Cover online suchen (MusicBrainz – sendet nur Interpret und Album)"))
        self.online_covers.setObjectName("online_covers")
        self.online_covers.setChecked(cfg.online_covers)
        root.addWidget(self.online_covers)

        head = PlainLabel(tr("Favoriten"))
        head.setObjectName("heading")
        root.addWidget(head)
        self.list = QListWidget()
        self.list.setObjectName("favorites")
        self.list.setMinimumHeight(240)
        root.addWidget(self.list, 3)

        self.fav_hint = PlainLabel("")
        self.fav_hint.setObjectName("fav_hint")
        self.fav_hint.setProperty("muted", True)
        self.fav_hint.setWordWrap(True)
        root.addWidget(self.fav_hint)

        row = QHBoxLayout()
        self.fav_buttons: dict[str, QPushButton] = {}
        for text, name, slot in ((tr("Hoch"), "up", lambda: self._move(-1)), (tr("Runter"), "down", lambda: self._move(1)),
                                 (tr("Umbenennen…"), "rename", self._ask_rename), (tr("Löschen"), "delete", self._delete),
                                 (tr("Adresse von Hand…"), "manual", self._ask_manual)):
            b = QPushButton(text)
            b.setObjectName(name)
            b.clicked.connect(slot)
            self.fav_buttons[name] = b
            row.addWidget(b)
        root.addLayout(row)

        head2 = PlainLabel(tr("Tastenkürzel"))
        head2.setObjectName("heading")
        root.addWidget(head2)
        hint = PlainLabel(tr("Funktionieren überall, auch wenn das Fenster zu ist. Alles geht auch per Maus."))
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
            edit.setMinimumWidth(125)  # „Ctrl+Alt+Space“ ganz lesbar, auch bei längeren englischen Namen
            self.hotkey_edits[key] = edit
            row, col = divmod(i, 2)
            grid.addWidget(PlainLabel(tr(label)), row, col * 2)
            grid.addWidget(edit, row, col * 2 + 1)
        root.addLayout(grid)

        pin_grid = QGridLayout()
        pin_grid.setHorizontalSpacing(10)
        pin_grid.setVerticalSpacing(4)
        self.hotkey_pins: list[QComboBox] = []
        for slot in range(HOTKEY_SLOTS):
            combo = QComboBox()
            combo.setObjectName(f"pin:{slot + 1}")
            self.hotkey_pins.append(combo)
            pin_grid.addWidget(PlainLabel(tr("Favorit {n} spielt:", n=slot + 1)), slot, 0)
            pin_grid.addWidget(combo, slot, 1)
        pin_grid.setColumnStretch(1, 1)
        root.addLayout(pin_grid)
        self._pins = list(cfg.hotkey_favorites)

        bottom = QHBoxLayout()
        bottom.addStretch()
        cancel = QPushButton(tr("Abbrechen"))
        cancel.setObjectName("cancel")
        cancel.clicked.connect(self.close)
        save_btn = QPushButton(tr("Speichern"))
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
        """Favoriten, die woanders (☆, Teufel-App) dazukamen, übernehmen – ohne eigene Änderungen zu verwerfen."""
        if self.cfg.anlage_favorites is not None:
            self._anlage = True
            row = self.list.currentRow()
            self._favs = [replace(f) for f in self.cfg.anlage_favorites if f.key not in self._removed]
            self._refresh_list(row if row >= 0 else None)
            return
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
            if f.on_anlage:
                label = tr(ANLAGE_LABELS.get(f.sub, "Teufel-Favorit"))
            else:
                label = tr(KIND_LABELS[f.kind]) if f.kind in KIND_LABELS else f.url
            self.list.addItem(f"{f.name}    —    {label}")
        if select is not None and self._favs:
            self.list.setCurrentRow(max(0, min(select, len(self._favs) - 1)))
        for name in ("rename", "manual"):
            self.fav_buttons[name].setVisible(not self._anlage)  # kann die Anlage nicht – nicht anbieten
        self.fav_buttons["delete"].setToolTip(
            tr("Entfernt den Favoriten auch in der Teufel-App") if self._anlage else "")
        self.fav_hint.setText(tr("Diese Liste ist dieselbe wie in der Teufel-App. Neue Favoriten über ☆ "
                                 "in Sendersuche oder Musik. Hoch/Runter sortiert auch die Teufel-App um.")
                              if self._anlage else "")
        self._fill_pins()

    def _pin_changed(self, slot: int) -> None:
        self._pins[slot] = self.hotkey_pins[slot].currentData() or ""

    def _fill_pins(self) -> None:
        for slot, combo in enumerate(self.hotkey_pins):
            want = self._pins[slot]
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(tr("Platz {n} der Liste", n=slot + 1), "")
            for f in self._favs:
                combo.addItem(f.name, f.key)
            if want and combo.findData(want) < 0:
                combo.addItem(tr("(nicht mehr in der Liste)"), want)
            combo.setCurrentIndex(max(0, combo.findData(want)))
            combo.blockSignals(False)
            if not combo.property("wired"):
                combo.currentIndexChanged.connect(lambda _i, s=slot: self._pin_changed(s))
                combo.setProperty("wired", True)

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        new = row + delta
        if 0 <= row < len(self._favs) and 0 <= new < len(self._favs):
            self._favs[row], self._favs[new] = self._favs[new], self._favs[row]
            self._refresh_list(new)

    def _delete(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._favs):
            if self._favs[row].on_anlage:
                self._removed.add(self._favs[row].key)
            del self._favs[row]
            self._refresh_list(row)

    def _ask_rename(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self._favs):
            return
        name, ok = QInputDialog.getText(self, tr("Umbenennen"), tr("Neuer Name:"), text=self._favs[row].name)
        if ok:
            self.rename_current(name)

    def _ask_manual(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Sender von Hand"), tr("Name:"))
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(self, tr("Sender von Hand"), tr("Stream-Adresse (http/https):"))
        if ok and not self.add_favorite(name, url):
            QMessageBox.warning(self, tr("Sender von Hand"), tr("Die Adresse muss mit http:// oder https:// beginnen."))

    def confirm_removal(self, names: list[str]) -> bool:
        box = QMessageBox(QMessageBox.Question, tr("Teufel-Favoriten"),
                          tr("Diese Favoriten werden auf der Anlage entfernt – auch in der Teufel-App:\n\n{names}",
                             names="\n".join(names)))
        box.setTextFormat(Qt.PlainText)  # Sendernamen kommen von TuneIn – nie als HTML auswerten
        box.setWindowIcon(app_icon())
        box.setStyleSheet(STYLESHEET)
        ok = box.addButton(tr("Entfernen"), QMessageBox.AcceptRole)
        box.addButton(tr("Abbrechen"), QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is ok

    def _save(self) -> None:
        before = list(self.cfg.active_favorites())
        removed = [f for f in before if f.on_anlage and f.key in self._removed]
        if removed and not self.confirm_removal([f.name for f in removed]):
            return
        self.cfg.title = self.title_edit.text().strip() or "DeskRemote"
        self.cfg.online_covers = self.online_covers.isChecked()
        old_language, self.cfg.language = self.cfg.language, self.language.currentData()
        if self._anlage:
            self.cfg.anlage_favorites = [replace(f) for f in self._favs]  # gleich weg; die Anlage bestätigt
        else:
            self.cfg.favorites = [replace(f) for f in self._favs]
        self.cfg.hotkey_favorites = list(self._pins)
        for key, edit in self.hotkey_edits.items():
            self.cfg.hotkeys[key] = edit.keySequence().toString(QKeySequence.PortableText)
        self.save(self.cfg)
        if removed:
            self.remove_on_anlage.emit([f.target for f in removed])
        if self._anlage and [f.key for f in self._favs] != [f.key for f in before if f.key not in self._removed]:
            self.reorder_on_anlage.emit([f.key for f in self._favs])
        self.config_changed.emit()
        self.close()
        if self.cfg.language != old_language and self.ask_restart():
            self.restart_requested.emit()

    def ask_restart(self) -> bool:
        """Neue Sprache greift erst nach Neustart – gefragt wird schon in der neuen Sprache."""
        previous = language()
        set_language(self.cfg.language)
        try:
            box = QMessageBox(QMessageBox.Question, tr("Sprache"),
                              tr("Die neue Sprache gilt nach einem Neustart von DeskRemote."))
            now = box.addButton(tr("Jetzt neu starten"), QMessageBox.AcceptRole)
            box.addButton(tr("Später"), QMessageBox.RejectRole)
        finally:
            set_language(previous)  # bis zum Neustart einheitlich in der bisherigen Sprache
        box.setWindowIcon(app_icon())
        box.setStyleSheet(STYLESHEET)
        box.exec()
        return box.clickedButton() is now

    def closeEvent(self, event):
        super().closeEvent(event)
        self.closed.emit()
