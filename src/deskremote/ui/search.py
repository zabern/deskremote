"""Sendersuche über radio-browser.info."""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..core.config import Config, Favorite
from ..core.radio import SearchError, Station
from .theme import app_icon, PlainLabel, ICON_PLAY, STYLESHEET

COUNTRIES = [("Österreich", "AT"), ("Deutschland", "DE"), ("Schweiz", "CH"), ("Alle Länder", None)]
DEBOUNCE_MS = 400


class SearchWindow(QWidget):
    play = Signal(object)  # Favorite
    favorites_changed = Signal()
    _results_ready = Signal(int, object)  # Generation, Liste oder Fehlertext

    def __init__(self, radio, cfg: Config, save: Callable[[Config], None], run_async: bool = True):
        super().__init__(None, Qt.Window)
        self.radio, self.cfg, self.save, self.run_async = radio, cfg, save, run_async
        self._generation = 0
        self._stations: list[Station] = []
        self.setWindowTitle("Sender suchen")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLESHEET)
        self.resize(460, 520)

        root = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setObjectName("query")
        self.query.setPlaceholderText("Sendername, z. B. FM4")
        self.country = QComboBox()
        self.country.setObjectName("country")
        for label, code in COUNTRIES:
            self.country.addItem(label, code)
        idx = next((i for i, (_, c) in enumerate(COUNTRIES) if c == cfg.search_country), 0)
        self.country.setCurrentIndex(idx)
        row.addWidget(self.query, 1)
        row.addWidget(self.country)
        root.addLayout(row)

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
        self.country.currentIndexChanged.connect(self._country_changed)
        self._results_ready.connect(self._show_results)

    # --- öffentlich -----------------------------------------------------
    def set_country(self, code: str | None) -> None:
        idx = next(i for i, (_, c) in enumerate(COUNTRIES) if c == code)
        self.country.setCurrentIndex(idx)

    def search_now(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._generation += 1
        gen, country = self._generation, self.country.currentData()
        self.status.setText("Suche …")

        def work():
            try:
                result: object = self.radio.search(text, country=country)
            except SearchError as exc:
                result = str(exc)
            except Exception:  # noqa: BLE001 – nie auf "Suche …" stehen bleiben
                result = "Suche fehlgeschlagen"
            self._results_ready.emit(gen, result)

        if self.run_async:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    # --- intern ---------------------------------------------------------
    def _country_changed(self) -> None:
        self.cfg.search_country = self.country.currentData() or ""
        if self.query.text().strip():
            self.search_now(self.query.text())

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
        self._stations = list(result)
        self.status.setText("" if self._stations else "Keine Sender gefunden")
        for i, st in enumerate(self._stations):
            self._list.insertWidget(i, self._row(i, st))

    def _row(self, i: int, st: Station) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        name = PlainLabel(st.name)
        name.setObjectName(f"name:{i}")
        info = PlainLabel(f"{st.codec} {st.bitrate}k" if st.bitrate else st.codec)
        info.setObjectName(f"info:{i}")
        info.setProperty("muted", True)
        if st.hls:
            info.setText(f"{info.text()} · spielt auf Raumfeld nicht".lstrip(" ·"))
        play = QPushButton(ICON_PLAY)
        play.setObjectName(f"play:{i}")
        play.setProperty("round", True)
        play.setProperty("icon_btn", True)
        play.setToolTip("Im Ziel-Raum abspielen")
        play.clicked.connect(lambda _=False, s=st: self.play.emit(self.radio.to_favorite(s)))
        star = QPushButton("☆")
        star.setObjectName(f"star:{i}")
        star.setProperty("round", True)
        star.setToolTip("Zu den Favoriten")
        star.clicked.connect(lambda _=False, s=st, b=star: self._add_favorite(s, b))
        if any(f.url == st.url for f in self.cfg.favorites):
            star.setText("★")
        if st.hls:  # HLS-Stream: Raumfeld kann ihn nicht abspielen
            for b in (play, star):
                b.setEnabled(False)
            w.setToolTip("HLS-Stream – den können die Raumfeld-Lautsprecher nicht abspielen")
        lay.addWidget(name, 1)
        lay.addWidget(info)
        lay.addWidget(play)
        lay.addWidget(star)
        return w

    def _add_favorite(self, st: Station, button: QPushButton) -> None:
        if any(f.url == st.url for f in self.cfg.favorites):
            return
        self.cfg.favorites.append(Favorite(st.name, st.url, st.favicon or ""))
        self.save(self.cfg)
        button.setText("★")
        self.favorites_changed.emit()
