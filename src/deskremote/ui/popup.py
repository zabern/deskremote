"""Ausklappfenster über dem Tray-Symbol."""

from __future__ import annotations

import html
import time
from itertools import groupby

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from ..i18n import tr
from .. import __version__
from ..core.anlage import RoomState
from ..core.config import Config
from .theme import (
    ICON_NEXT, ICON_PAUSE, ICON_PLAY, ICON_POWER, ICON_PREV, ICON_REPEAT_ALL, ICON_REPEAT_ONE, ICON_SEARCH,
    ICON_SHUFFLE, RED, SEPARATOR, SPOTIFY_ICON_PX, STYLESHEET, WHITE, PlainLabel, glyph_icon, spotify_icon_uri,
)


FAV_COLUMNS = 3



def spotify_icon_html() -> str:
    """Eigenes Spotify-Icon (data-URI) – das einzige Bild in formatierten Zeilen."""
    px = SPOTIFY_ICON_PX
    return f'<img src="{spotify_icon_uri(px)}" width="{px}" height="{px}" align="middle">&nbsp;'


def spotify_text(r: RoomState) -> str:
    """Spotify: „Titel · Interpret“, wenn die Anlage ihn liefert (Mehrraum-Modus), sonst „Spotify“."""
    if r.track:
        return f"{r.track} · {r.artist}" if r.artist else r.track
    return "Spotify"


def group_header_html(names: list[str], station: str, is_target: bool, spotify: bool = False) -> str:
    """Kopfzeile gekoppelter Räume: 'Raum + Raum · Sender' (bei Spotify mit Icon vor dem Titel).

    Alle Texte werden vollständig maskiert, damit aus Raum-, Sender- oder Titelnamen nie HTML
    werden kann; das einzige Bild ist unser eigenes Spotify-Icon.
    """
    rooms = html.escape(" + ".join(names), quote=True)
    color = RED if is_target else WHITE
    out = f'<span style="color:{color}; font-weight:600">{rooms}</span>'
    if station:
        icon = spotify_icon_html() if spotify else ""
        out += (f'<span style="color:{SEPARATOR}; font-weight:400">&nbsp;·&nbsp;</span>{icon}'
                f'<span style="color:{WHITE}; font-weight:400">{html.escape(station, quote=True)}</span>')
    # etwas Luft zwischen den Zeilen, falls umgebrochen wird
    return f'<p style="line-height:130%; margin:0">{out}</p>'


def now_playing(r: RoomState) -> str | None:
    """Album: „Titel · Interpret“, Radio: Sendername."""
    if r.is_queue and r.track:
        return f"{r.track} · {r.artist}" if r.artist else r.track
    return r.title


class ClickLabel(PlainLabel):
    """Anklickbares, umbrechendes Label (Kopfzeile gekoppelter Räume)."""

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.clicked.emit()
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        # Umgebrochener Text braucht mehr Höhe, als Qt dem Label von sich aus gibt
        super().resizeEvent(event)
        need = self.heightForWidth(self.width())
        if need > 0 and need != self.minimumHeight():
            self.setMinimumHeight(need)
            win = self.window()
            if win is not None and win.isVisible():
                QTimer.singleShot(0, win.adjustSize)


class FavButton(QPushButton):
    """Favoriten-Knopf: linksbündig, zu lange Namen hinten mit … gekürzt."""

    def __init__(self, full_text: str):
        super().__init__(full_text.replace("&", "&&"))
        self.full_text = full_text
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        avail = max(10, self.width() - 22)
        shown = self.fontMetrics().elidedText(self.full_text, Qt.ElideRight, avail)
        self.setText(shown.replace("&", "&&"))  # sonst macht Qt aus „&“ ein Tastenkürzel


def _repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


class Popup(QWidget):
    action = Signal(str, tuple)
    target_changed = Signal(str)
    open_search = Signal()
    open_music = Signal()
    open_settings = Signal()
    open_couple = Signal()
    shown = Signal()  # z. B. um die Teufel-Favoriten frisch zu holen

    def __init__(self, cfg: Config, covers=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.cfg = cfg
        self.covers = covers
        if covers is not None:
            covers.loaded.connect(self._on_logo)
        self.setObjectName("popup")
        self.setStyleSheet(STYLESHEET)
        self.setFixedWidth(400)
        self._rooms: list[RoomState] = []
        self._online = False
        self._auto_hidden_at = 0.0
        self.anchor_bottom: int | None = None  # gesetzt vom Tray: Fenster wächst dann nach oben

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        head = QHBoxLayout()
        self._heading = PlainLabel(cfg.title or "DeskRemote")
        self._heading.setObjectName("heading")
        head.addWidget(self._heading)
        head.addStretch()
        self._offline = PlainLabel(tr("Anlage nicht erreichbar"))
        self._offline.setObjectName("offline")
        head.addWidget(self._offline)
        version = PlainLabel(f"v{__version__}")
        version.setObjectName("version")
        version.setProperty("muted", True)
        head.addWidget(version)
        root.addLayout(head)

        self._rooms_box = QVBoxLayout()
        self._rooms_box.setSpacing(8)
        root.addLayout(self._rooms_box)

        target_row = QHBoxLayout()
        self._target_label = PlainLabel()
        self._target_label.setProperty("muted", True)
        target_row.addWidget(self._target_label)
        target_row.addStretch()
        root.addLayout(target_row)

        self._fav_grid = QGridLayout()
        self._fav_grid.setSpacing(6)
        for col in range(FAV_COLUMNS):
            self._fav_grid.setColumnStretch(col, 1)
        root.addLayout(self._fav_grid)

        # Meldungen erscheinen hier statt als Windows-Benachrichtigung und verschwinden von selbst
        self._message = PlainLabel()
        self._message.setObjectName("message")
        self._message.setWordWrap(True)
        self._message.hide()
        root.addWidget(self._message)
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self._clear_message)

        foot = QHBoxLayout()
        for text, sig, name in ((tr("Musik"), self.open_music, "open_music"),
                                (tr("Sendersuche"), self.open_search, "open_search"),
                                (tr("Räume koppeln"), self.open_couple, "open_couple"),
                                (tr("Einstellungen"), self.open_settings, "open_settings")):
            b = QPushButton(text)
            b.setObjectName(name)
            b.setProperty("flat_link", True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(sig.emit)
            if name == "open_search":
                b.setIcon(glyph_icon(ICON_SEARCH))
            foot.addWidget(b)
        foot.addStretch()
        root.addLayout(foot)

        self.set_favorites()
        self.set_online(False)

    def showEvent(self, event):
        super().showEvent(event)
        self.shown.emit()

    # --- öffentlich -----------------------------------------------------
    def set_online(self, online: bool) -> None:
        self._online = online
        self._offline.setVisible(not online)
        self._apply_enabled()

    def set_state(self, rooms: list[RoomState]) -> None:
        if any(s.isSliderDown() for s in self.findChildren(QSlider)):
            return  # nicht unter dem Finger umbauen
        if list(rooms) == self._rooms:
            return  # nichts geändert – Klicks und Fokus nicht stören
        self._rooms = list(rooms)
        self._rebuild_rooms()
        self._update_target_label()
        self._apply_enabled()
        if self.isVisible():
            QTimer.singleShot(0, self.adjustSize)  # Inhalt geändert → Höhe neu bemessen

    def show_message(self, text: str, timeout_ms: int = 6000) -> None:
        self._message.setText(text)
        self._message.show()
        self._message_timer.start(timeout_ms)

    def _clear_message(self) -> None:
        self._message.hide()
        self._message.clear()
        self.adjustSize()

    def _on_logo(self, key: str, pix) -> None:
        for b in self.findChildren(QPushButton):
            if b.property("logo_key") == key:
                b.setIcon(QIcon(pix))

    def apply_config(self) -> None:
        """Nach Änderungen in den Einstellungen: Überschrift und Favoriten neu."""
        self._heading.setText(self.cfg.title or "DeskRemote")
        self.set_favorites()

    def set_favorites(self) -> None:
        while self._fav_grid.count():
            w = self._fav_grid.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for i, fav in enumerate(self.cfg.active_favorites()):
            b = FavButton(fav.name)
            b.setObjectName(f"fav:{i}")
            b.setProperty("fav", True)
            b.setToolTip(html.escape(fav.name))  # Tooltips wertet Qt sonst als HTML aus
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, f=fav: self._play_favorite(f))
            if fav.is_music and fav.cover and self.covers is not None:
                key = f"fav:{fav.url}"  # Cover vom Raumfeld-Host statt Senderlogo
                b.setProperty("logo_key", key)
                # server_host=None: nur private, nicht-loopback Adressen (One M) bzw. deren HTTPS-Weiterleitung
                pix = self.covers.request(key, fav.cover, size=16, server_host=None)
                if pix is not None:
                    b.setIcon(QIcon(pix))
            elif fav.logo and self.covers is not None and not fav.is_music:
                b.setProperty("logo_key", f"logo:{fav.url}")
                pix = self.covers.request_logo(f"logo:{fav.url}", fav.logo)
                if pix is not None:
                    b.setIcon(QIcon(pix))
            self._fav_grid.addWidget(b, i // FAV_COLUMNS, i % FAV_COLUMNS)
        self.adjustSize()
        self._apply_enabled()

    # --- intern ---------------------------------------------------------
    def _play_favorite(self, fav) -> None:
        if self.cfg.target_room:
            self.action.emit("play_favorite", (self.cfg.target_room, fav))

    def _toggle(self, button: QPushButton, room_udn: str) -> None:
        """Symbol sofort umschalten; der nächste Zustand von der Anlage bestätigt oder korrigiert."""
        playing = not bool(button.property("playing"))
        button.setProperty("playing", playing)
        button.setText(ICON_PAUSE if playing else ICON_PLAY)
        _repolish(button)
        self.action.emit("toggle_play", (room_udn,))

    def _set_target(self, udn: str) -> None:
        self.cfg.target_room = udn
        self._mark_targets()
        self._update_target_label()
        self.target_changed.emit(udn)

    def _mark_targets(self) -> None:
        for b in self.findChildren(QPushButton) + self.findChildren(QLabel):
            if b.objectName().startswith("target:"):
                b.setProperty("target", self.cfg.target_room in (b.property("rooms") or []))
                if isinstance(b, ClickLabel):
                    b.setText(group_header_html(b.property("names"), b.property("station"),
                                                b.property("target"), bool(b.property("spotify"))))
                _repolish(b)

    def _target_members(self) -> list[RoomState]:
        target = next((r for r in self._rooms if r.udn == self.cfg.target_room), None)
        if target is None:
            return []
        if target.zone_udn is None:
            return [target]
        return [r for r in self._rooms if r.zone_udn == target.zone_udn]

    def _update_target_label(self) -> None:
        members = self._target_members()
        names = " + ".join(r.name for r in members)
        self._target_label.setText(tr("Favoriten · Ziel: {names}", names=names) if names else tr("Favoriten · Ziel: Raum anklicken"))

    def _apply_enabled(self) -> None:
        has_target = bool(self._target_members())
        for b in self.findChildren(QPushButton):
            n = b.objectName()
            if n.startswith("fav:"):
                b.setEnabled(self._online and has_target)
            elif n.startswith(("standby:", "target:")):
                b.setEnabled(self._online)
            elif n.startswith("play:"):
                b.setEnabled(self._online and b.property("can_play") is True)
            elif n.startswith(("prev:", "next:", "repeat:")):
                b.setEnabled(self._online)
            elif n.startswith("shuffle:"):
                b.setEnabled(self._online and b.property("can_use") is not False)
        for s in self.findChildren(QSlider):
            s.setEnabled(self._online and s.property("has_volume") is True)
        for lbl in self.findChildren(ClickLabel):
            lbl.setEnabled(self._online)

    def _clear_rooms(self) -> None:
        while self._rooms_box.count():
            w = self._rooms_box.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _rebuild_rooms(self) -> None:
        self._clear_rooms()

        def key(r: RoomState) -> str:
            return r.zone_udn or r.udn

        ordered = sorted(self._rooms, key=lambda r: (min(x.name.lower() for x in self._rooms if key(x) == key(r)),
                                                     r.name.lower()))
        for zkey, members in groupby(ordered, key=key):
            members = list(members)
            card = QFrame()
            card.setObjectName("card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(12, 10, 12, 10)
            lay.setSpacing(6)
            if len(members) > 1:
                self._group_card(lay, zkey, members)
            else:
                self._single_card(lay, members[0])
            self._rooms_box.addWidget(card)

    # Bausteine ---------------------------------------------------------
    def _play_button(self, key: str, r: RoomState) -> QPushButton:
        play = QPushButton(ICON_PAUSE if r.playing else ICON_PLAY)
        play.setProperty("icon_btn", True)
        play.setObjectName(f"play:{key}")
        play.setProperty("round", True)
        play.setProperty("playing", r.playing)
        play.setProperty("can_play", r.zone_udn is not None or r.can_toggle)
        play.setToolTip("Play/Pause" if r.zone_udn or r.can_toggle else tr("Hier läuft nichts – Sender wählen"))
        play.setCursor(Qt.PointingHandCursor)
        play.clicked.connect(lambda _=False, u=r.udn, b=play: self._toggle(b, u))
        return play

    def _header(self, key: str, members: list[RoomState], tooltip: str) -> ClickLabel:
        """Kopfzeile „Raum (+ Raum) · was läuft“ – für einzelne Räume und Gruppen gleich."""
        first = members[0]
        rooms = [r.udn for r in members]
        names = [r.name for r in members]
        station = spotify_text(first) if first.spotify else (now_playing(first) or "")
        is_target = self.cfg.target_room in rooms
        head = ClickLabel()
        head.setTextFormat(Qt.RichText)  # nur maskierter Text, siehe group_header_html
        head.setObjectName(f"target:{key}")
        head.setProperty("group", True)
        head.setProperty("rooms", rooms)
        head.setProperty("names", names)
        head.setProperty("station", station)
        head.setProperty("spotify", first.spotify)
        head.setProperty("target", is_target)
        head.setText(group_header_html(names, station, is_target, first.spotify))
        head.setWordWrap(True)
        head.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.setCursor(Qt.PointingHandCursor)
        head.setToolTip(tooltip)
        head.clicked.connect(lambda u=rooms[0]: self._set_target(u))
        return head


    def _power_button(self, r: RoomState) -> QPushButton:
        standby = r.power.endswith("STANDBY")
        power = QPushButton(ICON_POWER)
        power.setProperty("icon_btn", True)
        power.setProperty("standby", standby)
        power.setProperty("on", not standby)
        power.setObjectName(f"standby:{r.udn}")
        power.setProperty("round", True)
        room = html.escape(r.name)  # Tooltips wertet Qt sonst als HTML aus
        power.setToolTip(tr("{room} einschalten", room=room) if standby else tr("{room} in Standby", room=room))
        power.setCursor(Qt.PointingHandCursor)
        power.clicked.connect(lambda _=False, u=r.udn, s=standby: self.action.emit("wake" if s else "standby", (u,)))
        return power

    def _volume_slider(self, r: RoomState) -> QSlider:
        slider = QSlider(Qt.Horizontal)
        slider.setObjectName(f"vol:{r.udn}")
        slider.setRange(0, 100)
        slider.setProperty("has_volume", r.volume is not None)
        slider.blockSignals(True)
        slider.setValue(r.volume or 0)
        slider.blockSignals(False)
        slider.valueChanged.connect(lambda v, u=r.udn: self.action.emit("set_volume", (u, v)))
        return slider

    def _mode_button(self, glyph: str, name: str, active: bool, tip: str) -> QPushButton:
        b = QPushButton(glyph)
        b.setObjectName(name)
        b.setProperty("round", True)
        b.setProperty("icon_btn", True)
        b.setProperty("on", active)        # aktiv: weiß mit Ring
        b.setProperty("standby", not active)  # aus: gedämpft
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _queue_row(self, key: str, r: RoomState, indent: bool = True) -> QHBoxLayout:
        """⏮ ⏭ 🔀 🔁 – nur wenn ein Album/Ordner läuft."""
        row = QHBoxLayout()
        row.setSpacing(8)
        if indent:
            row.addSpacing(38)
        prev = self._mode_button(ICON_PREV, f"prev:{key}", False, tr("Vorheriger Titel"))
        prev.setProperty("standby", False)
        prev.clicked.connect(lambda _=False, u=r.udn: self.action.emit("previous_track", (u,)))
        nxt = self._mode_button(ICON_NEXT, f"next:{key}", False, tr("Nächster Titel"))
        nxt.setProperty("standby", False)
        nxt.clicked.connect(lambda _=False, u=r.udn: self.action.emit("next_track", (u,)))
        shuffle = self._mode_button(ICON_SHUFFLE, f"shuffle:{key}", r.shuffle,
                                    tr("Zufall") if r.repeat != "one" else tr("Zufall (nicht mit „einen wiederholen“)"))
        shuffle.setProperty("can_use", r.repeat != "one")
        shuffle.clicked.connect(lambda _=False, s=r: self.action.emit("set_play_mode", (s.udn, not s.shuffle, s.repeat)))
        repeat = self._mode_button(ICON_REPEAT_ONE if r.repeat == "one" else ICON_REPEAT_ALL, f"repeat:{key}",
                                   r.repeat != "off", tr({"off": "Wiederholen: aus", "all": "Wiederholen: alle",
                                                       "one": "Wiederholen: einen Titel"}[r.repeat]))
        nxt_mode = {"off": "all", "all": "one", "one": "off"}[r.repeat]
        repeat.clicked.connect(lambda _=False, s=r, m=nxt_mode: self.action.emit("set_play_mode", (s.udn, s.shuffle, m)))
        for b in (prev, nxt, shuffle, repeat):
            row.addWidget(b)
        row.addStretch()
        return row

    def _single_card(self, lay: QVBoxLayout, r: RoomState) -> None:
        # Kopfzeile wie bei der Gruppe: „Raum · Titel“, Titel läuft unter dem Namen weiter (Abnahme v2.2)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self._play_button(r.zone_udn or r.udn, r), 0, Qt.AlignTop)
        top.addWidget(self._header(r.udn, [r], tr("Als Ziel für Sender und Tastenkürzel wählen")), 1)
        top.addWidget(self._power_button(r), 0, Qt.AlignTop)
        lay.addLayout(top)
        if r.is_queue:
            lay.addLayout(self._queue_row(r.zone_udn or r.udn, r))
        lay.addWidget(self._volume_slider(r))

    def _group_card(self, lay: QVBoxLayout, zone: str, members: list[RoomState]) -> None:
        """Gekoppelte Räume: eine Kopfzeile für die Gruppe, darunter je Raum Lautstärke und Standby."""
        first = members[0]
        # Raster: Spalte 0 = Play, Spalte 1 = Gruppen-/Raumnamen (gleiche Kante), 2 = Regler, 3 = Standby
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(2, 1)
        grid.addWidget(self._play_button(zone, first), 0, 0, Qt.AlignTop)
        head = self._header(zone, members, tr("Gruppe als Ziel für Sender und Tastenkürzel wählen"))
        grid.addWidget(head, 0, 1, 1, 3)
        first_row = 1
        if first.is_queue:
            grid.addLayout(self._queue_row(zone, first, indent=False), 1, 1, 1, 3)
            first_row = 2
        for i, r in enumerate(members, start=first_row):
            name = PlainLabel(r.name)
            name.setObjectName(f"member:{r.udn}")
            name.setProperty("member", True)
            name.setFixedWidth(96)
            grid.addWidget(name, i, 1)
            grid.addWidget(self._volume_slider(r), i, 2)
            grid.addWidget(self._power_button(r), i, 3)
        lay.addLayout(grid)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.anchor_bottom is not None and self.isVisible():
            self.move(self.x(), self.anchor_bottom - self.height() + 1)

    def changeEvent(self, event):  # schließt, wenn ein anderes Fenster aktiv wird
        super().changeEvent(event)
        if event.type() == event.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            self.hide()
            self.mark_auto_hidden()

    def mark_auto_hidden(self) -> None:
        self._auto_hidden_at = time.monotonic()

    def recently_auto_hidden(self, window: float = 0.4) -> bool:
        return time.monotonic() - self._auto_hidden_at < window
