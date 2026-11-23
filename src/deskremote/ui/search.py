"""Sendersuche über TuneIn auf der Raumfeld-Anlage – ☆ legt einen Teufel-Favoriten an."""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..core.config import Config, Favorite
from ..i18n import tr
from .theme import app_icon, PlainLabel, ICON_PLAY, STYLESHEET

DEBOUNCE_MS = 400


class SearchWindow(QWidget):
    play = Signal(object)  # Favorite (Suchtreffer, direkt abspielen)
    add_requested = Signal(str)  # Objekt-ID → in die Teufel-Favoriten
    _results_ready = Signal(int, object)  # Generation, Liste oder Fehlertext

    def __init__(self, search: Callable[[str], list[Favorite]], cfg: Config, run_async: bool = True):
        super().__init__(None, Qt.Window)
        self.search, self.cfg, self.run_async = search, cfg, run_async
        self._generation = 0
        self._hits: list[Favorite] = []
        self._pending: set[str] = set()
        self.setWindowTitle(tr("Sender suchen"))
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLESHEET)
        self.resize(460, 520)

        root = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setObjectName("query")
        self.query.setPlaceholderText(tr("Sendername, z. B. FM4"))
        root.addWidget(self.query)
        hint = PlainLabel(tr("Sucht bei TuneIn über die Anlage. ☆ legt den Sender in den Teufel-Favoriten an."))
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.status = PlainLabel("")
        self.status.setObjectName("status")
        self.status.setProperty("muted", True)
        root.addWidget(self.status)

        self._list_host = QWidget()
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(4)
        self._list.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_host)
        root.addWidget(scroll, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(lambda: self.search_now(self.query.text()))
        self.query.textChanged.connect(lambda _t: self._timer.start())
        self._results_ready.connect(self._show_results)

    # --- öffentlich -----------------------------------------------------
    def search_now(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._generation += 1
        gen = self._generation
        self.status.setText(tr("Suche …"))

        def work():
            try:
                result: object = self.search(text)
            except Exception:  # noqa: BLE001 – nie auf "Suche …" stehen bleiben
                result = tr("Suche fehlgeschlagen – ist die Anlage erreichbar?")
            self._results_ready.emit(gen, result)

        if self.run_async:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    def refresh_stars(self) -> None:
        """Nach neuer Teufel-Liste: Sterne passend füllen (auch für Änderungen aus der Teufel-App)."""
        on_anlage = self._targets()
        self._pending -= on_anlage
        for star in self.findChildren(QPushButton):
            target = star.property("target")
            if target:
                star.setText("★" if target in on_anlage else ("…" if target in self._pending else "☆"))

    # --- intern ---------------------------------------------------------
    def _targets(self) -> set[str]:
        return {f.target for f in (self.cfg.anlage_favorites or []) if f.on_anlage}

    def _clear(self) -> None:
        while self._list.count() > 1:
            w = self._list.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _show_results(self, gen: int, result) -> None:
        if gen != self._generation:
            return  # veraltete Antwort
        self._clear()
        if isinstance(result, str):
            self.status.setText(result)
            return
        self._hits = list(result)
        self.status.setText("" if self._hits else tr("Keine Sender gefunden"))
        for i, hit in enumerate(self._hits):
            self._list.insertWidget(i, self._row(i, hit))

    def _row(self, i: int, hit: Favorite) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        name = PlainLabel(hit.name)
        name.setObjectName(f"name:{i}")
        play = QPushButton(ICON_PLAY)
        play.setObjectName(f"play:{i}")
        play.setProperty("round", True)
        play.setProperty("icon_btn", True)
        play.setToolTip(tr("Im Ziel-Raum abspielen"))
        play.clicked.connect(lambda _=False, f=hit: self.play.emit(f))
        star = QPushButton("★" if hit.target in self._targets() else "☆")
        star.setObjectName(f"star:{i}")
        star.setProperty("round", True)
        star.setProperty("target", hit.target)
        star.setToolTip(tr("Zu den Teufel-Favoriten (auch in der Teufel-App)"))
        star.clicked.connect(lambda _=False, f=hit, b=star: self._add_favorite(f, b))
        lay.addWidget(name, 1)
        lay.addWidget(play)
        lay.addWidget(star)
        return w

    def _add_favorite(self, hit: Favorite, button: QPushButton) -> None:
        if hit.target in self._targets():
            return  # erneutes Klicken auf „…“ ist harmlos: die Anlage legt nie doppelt an
        self._pending.add(hit.target)
        button.setText("…")  # die Anlage bestätigt mit der neuen Liste
        self.add_requested.emit(hit.target)
