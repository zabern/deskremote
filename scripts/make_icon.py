"""Erzeugt assets/icon.ico aus dem gezeichneten Tray-Symbol."""

import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication

from deskremote.ui.theme import tray_icon

app = QGuiApplication(sys.argv)
out = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
tray_icon(True).pixmap(256, 256).save(str(out), "ICO")
print(out)
