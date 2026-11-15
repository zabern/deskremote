"""Gestaltung: Schwarz als Grundton, Weiß für Schrift, Rot als Akzent."""

from __future__ import annotations

import base64

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel

BLACK = "#000000"
CARD = "#111111"
WHITE = "#FFFFFF"
MUTED = "rgba(255,255,255,0.6)"
RED = "#E2001A"

# Symbole aus den Windows-Symbolschriften (Segoe Fluent Icons / MDL2 Assets)
ICON_PLAY = "\uE768"
ICON_PAUSE = "\uE769"
ICON_POWER = "\uE7E8"
ICON_SEARCH = "\uE721"
ICON_PREV = "\uE892"
ICON_NEXT = "\uE893"
ICON_SHUFFLE = "\uE8B1"
ICON_REPEAT_ALL = "\uE8EE"
ICON_REPEAT_ONE = "\uE8ED"
OFFLINE_GREY = "#7A7A7A"
SEPARATOR = "#8C8C8C"  # kleiner Mittelpunkt zwischen Räumen und Sender  # einziges Grau: Tray-Symbol, wenn die Anlage weg ist

STYLESHEET = f"""
QWidget {{ background: {BLACK}; color: {WHITE}; font-family: "Segoe UI"; font-size: 10pt; }}
QFrame#card {{ background: {CARD}; border-radius: 10px; }}
QFrame#card QLabel, QFrame#card QPushButton[room="true"] {{ background: transparent; }}
QPushButton[icon_btn="true"] {{ font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets"; font-size: 10pt; }}
QPushButton[standby="true"] {{ color: rgba(255,255,255,0.35); border-color: transparent; }}
QPushButton[on="true"] {{ color: {WHITE}; border: 1px solid {WHITE}; }}
QLabel#muted, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#offline {{ color: {RED}; font-weight: 600; }}
QLabel#message {{ color: {RED}; }}
QLabel#heading {{ font-size: 11pt; font-weight: 600; }}
QPushButton {{ background: transparent; color: {WHITE}; border: 1px solid #2A2A2A; border-radius: 14px;
               padding: 4px 12px; }}
QPushButton:hover {{ border-color: {RED}; }}
QPushButton:focus {{ border-color: {RED}; outline: none; }}
QPushButton:disabled {{ color: rgba(255,255,255,0.3); border-color: #1A1A1A; }}
QPushButton[flat_link="true"] {{ border: none; padding: 2px 4px; color: {MUTED}; }}
QPushButton[flat_link="true"]:hover {{ color: {WHITE}; }}
QPushButton[room="true"] {{ border: none; padding: 2px 0; text-align: left; font-weight: 600; }}
QPushButton[room="true"][target="true"] {{ color: {RED}; }}
QPushButton[round="true"] {{ min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
                             padding: 0; border-radius: 14px; }}
QPushButton[playing="true"] {{ background: {RED}; border-color: {RED}; }}
QPushButton[fav="true"] {{ border-color: #333333; text-align: left; padding-left: 10px; }}
QLabel[group="true"] {{ font-weight: 600; }}
QLabel[group="true"][target="true"] {{ color: {RED}; }}
QLabel[member="true"] {{ color: {WHITE}; }}
QPushButton[fav="true"]:hover {{ background: {RED}; border-color: {RED}; }}
QSlider::groove:horizontal {{ height: 4px; background: #333333; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {RED}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {WHITE}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:disabled {{ background: #444444; }}
QSlider::sub-page:horizontal:disabled {{ background: #444444; }}
QLineEdit, QComboBox, QListWidget, QKeySequenceEdit {{ background: {CARD}; border: 1px solid #2A2A2A;
               border-radius: 6px; padding: 4px 6px; selection-background-color: {RED}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {RED}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #555555; border-radius: 3px; }}
QCheckBox::indicator:checked {{ background: {RED}; border-color: {RED}; }}
QMenu {{ background: {CARD}; border: 1px solid #2A2A2A; }}
QMenu::item:selected {{ background: {RED}; }}
"""


class PlainLabel(QLabel):
    """QLabel, das Text immer als reinen Text zeigt.

    Qt wertet Text sonst automatisch als HTML aus (Qt.AutoText). Ein Sendername wie
    "<img src='file://server/x.png'>" würde dann eine Netzwerkverbindung auslösen.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self.setText(text)


def glyph_icon(glyph: str, color: str = WHITE, size: int = 32) -> QIcon:
    """Symbol aus der Windows-Symbolschrift als Icon (einfarbig, passend zum Look)."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    font = QFont()
    font.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
    font.setPixelSize(int(size * 0.75))
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, glyph)
    p.end()
    return QIcon(pix)


_CHECK_PATH: str | None = None


def checkbox_style() -> str:
    """Weißer Haken im roten Kästchen (Qt-Stylesheets brauchen dafür eine Bilddatei).

    Die Datei liegt in einem eigenen, pro Programmstart frisch angelegten Verzeichnis –
    eine vorhandene Datei mit festem Namen wird nie wiederverwendet.
    """
    global _CHECK_PATH
    if _CHECK_PATH is None:
        import tempfile
        from pathlib import Path

        folder = Path(tempfile.mkdtemp(prefix="deskremote-"))
        path = folder / "check.png"
        pix = QPixmap(32, 32)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(WHITE), 4.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPointF(7, 17), QPointF(13, 23), QPointF(25, 9)])
        p.end()
        pix.save(str(path), "PNG")
        _CHECK_PATH = path.as_posix()
    return f"QCheckBox::indicator:checked {{ background: {RED}; border-color: {RED}; image: url({_CHECK_PATH}); }}"


def app_icon() -> QIcon:
    """Fenster- und Taskleistensymbol."""
    return tray_icon(True)


SPOTIFY_ICON_PX = 13  # passt zur 10-pt-Schrift
_spotify_uris: dict[int, str] = {}


def spotify_icon_pixmap(px: int) -> QPixmap:
    """Spotify-Zeichen einfarbig weiß: Kreis mit drei ausgesparten Bögen (4-fach gezeichnet, scharf auf HiDPI)."""
    s = px * 4
    pix = QPixmap(s, s)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(WHITE))
    p.drawEllipse(QRectF(0, 0, s, s))
    p.setCompositionMode(QPainter.CompositionMode_Clear)  # Bögen durchsichtig, egal welcher Hintergrund
    p.setBrush(Qt.NoBrush)
    for y, half, sag, width in ((0.34, 0.29, 0.07, 0.095), (0.52, 0.245, 0.055, 0.075), (0.68, 0.20, 0.045, 0.06)):
        p.setPen(QPen(QColor(BLACK), width * s, Qt.SolidLine, Qt.RoundCap))
        path = QPainterPath(QPointF((0.5 - half) * s, (y + sag) * s))
        path.quadTo(QPointF(0.5 * s, (y - sag) * s), QPointF((0.5 + half) * s, (y + sag) * s))
        p.drawPath(path)
    p.end()
    return pix


def spotify_icon_uri(px: int = SPOTIFY_ICON_PX) -> str:
    """Icon als data-URI für Rich Text – eigenes Bild, nie eine Adresse von außen."""
    if px not in _spotify_uris:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        spotify_icon_pixmap(px).save(buf, "PNG")
        _spotify_uris[px] = "data:image/png;base64," + base64.b64encode(bytes(data)).decode("ascii")
    return _spotify_uris[px]


def tray_icon(online: bool) -> QIcon:
    """Schlichtes Lautsprecher-Symbol mit Schallwellen; online mit rotem Punkt, offline grau."""
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(BLACK))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), 14, 14)
    fg = QColor(WHITE if online else OFFLINE_GREY)
    body = QPainterPath()
    body.moveTo(12, 26)
    body.lineTo(22, 26)
    body.lineTo(34, 15)
    body.lineTo(34, 49)
    body.lineTo(22, 38)
    body.lineTo(12, 38)
    body.closeSubpath()
    p.setBrush(fg)
    p.drawPath(body)
    pen = QPen(fg, 4, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(30, 22, 14, 20), -50 * 16, 100 * 16)
    p.drawArc(QRectF(30, 14, 24, 36), -50 * 16, 100 * 16)
    if online:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(RED))
        p.drawEllipse(QPointF(51, 51), 8, 8)
    p.end()
    return QIcon(pix)
