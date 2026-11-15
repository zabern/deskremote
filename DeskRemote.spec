# -*- mode: python ; coding: utf-8 -*-
# Build-Datei für dist\DeskRemote.exe (aufgerufen von build.ps1).
#
# Schlank: DeskRemote nutzt nur QtCore, QtGui, QtWidgets.
# Bewusst OHNE SVG: SVG-Bilder können fremde Dateien nachladen, auch \\host\… per SMB → NTLM-Leak
# (Sicherheits-Audit run-2, F1). Der Code lehnt SVG ab (covers.decode_image); hier zusätzlich das Plugin.
# PyInstaller packt sonst große Teile von Qt mit ein (Software-OpenGL, QML/Quick, PDF,
# Übersetzungen, Qt-Netzwerk samt eigener OpenSSL) – die werden hier ausgefiltert.
# Fehlt nach einer PySide6-Aktualisierung etwas, merkt man es beim Start bzw. an fehlenden Bildern.

QT_MODULES_UNUSED = [
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtOpenGL", "PySide6.QtPdf",
    "PySide6.QtVirtualKeyboard", "PySide6.QtSvg",
]

# Ziel-Dateinamen (klein geschrieben, Teilstring), die nicht in die exe sollen
DROP = [
    "opengl32sw.dll",
    "qt6opengl.dll", "qt6network.dll", "qt6pdf.dll", "qt6virtualkeyboard.dll",
    "qt6qml", "qt6quick", "qt6svg.dll",
    "pyside6\\plugins\\imageformats\\qsvg.dll", "pyside6\\plugins\\iconengines\\qsvgicon.dll",
    "pyside6\\translations\\",
    "pyside6\\plugins\\tls\\", "pyside6\\plugins\\networkinformation\\",
    "pyside6\\plugins\\platforminputcontexts\\", "pyside6\\plugins\\generic\\",
    "pyside6\\plugins\\platforms\\qdirect2d.dll", "pyside6\\plugins\\platforms\\qminimal.dll",
    "pyside6\\plugins\\platforms\\qoffscreen.dll",
    "pyside6\\plugins\\imageformats\\qpdf.dll", "pyside6\\plugins\\imageformats\\qtiff.dll",
    "pyside6\\plugins\\imageformats\\qicns.dll", "pyside6\\plugins\\imageformats\\qtga.dll",
    "pyside6\\plugins\\imageformats\\qwbmp.dll",
    "pyside6\\libcrypto-3.dll", "pyside6\\libssl-3.dll",  # Qts OpenSSL; Python bringt eigene (-x64) mit
    "pyside6\\qtnetwork.pyd",
]


def keep(entry):
    name = entry[0].replace("/", "\\").lower()
    return not any(d in name for d in DROP)


a = Analysis(
    ['src/deskremote/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=QT_MODULES_UNUSED,
    noarchive=False,
    optimize=0,
)
a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DeskRemote',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
