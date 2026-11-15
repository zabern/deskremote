"""Räume koppeln und trennen – zeigt den aktuellen Stand und meldet das Ergebnis."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ..core.anlage import RoomState
from .theme import STYLESHEET, PlainLabel, app_icon, checkbox_style


def current_group(rooms: list[RoomState], target: str | None) -> list[RoomState]:
    """Die gekoppelte Gruppe, um die es geht: die des Ziel-Raums, sonst die größte."""
    zones: dict[str, list[RoomState]] = defaultdict(list)
    for r in rooms:
        if r.zone_udn:
            zones[r.zone_udn].append(r)
    groups = [sorted(m, key=lambda r: r.name.lower()) for m in zones.values() if len(m) > 1]
    for g in groups:
        if any(r.udn == target for r in g):
            return g
    return max(groups, key=len) if groups else []


class CoupleDialog(QWidget):
    action = Signal(str, tuple)

    def __init__(self, rooms: list[RoomState], target_room: str | None):
        super().__init__(None, Qt.Window)
        self.setWindowTitle("Räume koppeln")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLESHEET + checkbox_style())
        self.resize(380, 300)
        self._target = target_room
        self._rooms: list[RoomState] = []
        self._group: list[str] = []
        self._editing = False  # Nutzer hat Haken geändert, aber noch nicht übernommen

        root = QVBoxLayout(self)
        head = PlainLabel("Diese Räume gemeinsam abspielen:")
        head.setObjectName("heading")
        root.addWidget(head)
        hint = PlainLabel("Haken setzen koppelt, Haken entfernen trennt.")
        hint.setProperty("muted", True)
        root.addWidget(hint)

        self._box_layout = QVBoxLayout()
        root.addLayout(self._box_layout)
        self._boxes: dict[str, QCheckBox] = {}
        root.addStretch()

        bottom = QHBoxLayout()
        self.status = PlainLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        self.apply = QPushButton("Übernehmen")
        self.apply.setObjectName("apply")
        self.apply.clicked.connect(self._apply)
        bottom.addWidget(self.apply)
        root.addLayout(bottom)

        self.set_rooms(rooms)

    # --- öffentlich -----------------------------------------------------
    def set_rooms(self, rooms: list[RoomState]) -> None:
        """Neuer Stand von der Anlage: Haken und Statuszeile anpassen."""
        self._rooms = sorted(rooms, key=lambda r: r.name.lower())
        group = current_group(self._rooms, self._target)
        self._group = [r.udn for r in group]
        if group and self._target not in self._group:
            self._target = group[0].udn
        for r in self._rooms:
            if r.udn not in self._boxes:
                box = QCheckBox(r.name)
                box.setObjectName(f"couple:{r.udn}")
                box.toggled.connect(self._on_toggle)
                self._boxes[r.udn] = box
                self._box_layout.addWidget(box)
        if not self._editing:  # angefangene Auswahl nicht durch die laufende Aktualisierung überschreiben
            for udn, box in self._boxes.items():
                box.blockSignals(True)
                box.setChecked(udn in self._group)
                box.blockSignals(False)
        names = " + ".join(r.name for r in group)
        self.status.setText(f"Gekoppelt: {names}" if names else "Keine Räume gekoppelt")
        self._update()

    # --- intern ---------------------------------------------------------
    def _selected(self) -> list[str]:
        return sorted(u for u, b in self._boxes.items() if b.isChecked())

    def _on_toggle(self) -> None:
        self._editing = set(self._selected()) != set(self._group)
        self._update()

    def _update(self) -> None:
        sel = self._selected()
        changed = set(sel) != set(self._group)
        allowed = len(sel) >= 2 or bool(self._group)  # neue Gruppe braucht 2 Räume; Auflösen geht immer
        self.apply.setEnabled(changed and allowed)

    def _apply(self) -> None:
        sel = self._selected()
        if len(sel) >= 2:
            self.action.emit("couple", (sel,))
            for udn in self._group:
                if udn not in sel:
                    self.action.emit("uncouple", (udn,))
        else:
            # Gruppe auflösen wie die Teufel-App: der letzte Raum behält die Zone. Würden alle Räume
            # zonenlos, gäbe es evtl. gar keine Zone mehr – und damit kein Spotify-Gerät.
            keep = sel[0] if sel else (self._target if self._target in self._group else self._group[0])
            for udn in self._group:
                if udn != keep:
                    self.action.emit("uncouple", (udn,))
        if sel:
            self._target = sel[0]
        self._editing = False  # ab jetzt zeigt das Fenster wieder den Stand der Anlage
        self.status.setText("Wird übernommen …")
        self.apply.setEnabled(False)
