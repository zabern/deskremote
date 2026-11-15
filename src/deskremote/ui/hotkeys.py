"""Globale Tastenkürzel über die Windows-API (RegisterHotKey / WM_HOTKEY)."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

from ..core.config import Config

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312

_MODS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "meta": MOD_WIN, "win": MOD_WIN}
_KEYS = {"up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "space": 0x20}
_KEYS.update({str(d): 0x30 + d for d in range(10)})
_KEYS.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
_KEYS.update({f"f{n}": 0x6F + n for n in range(1, 13)})


class NoTarget(Exception):
    pass


def parse_hotkey(s: str) -> tuple[int, int]:
    parts = [p.strip().lower() for p in s.split("+")]
    if not s.strip() or any(not p for p in parts):
        raise ValueError(f"Ungültiges Tastenkürzel: {s!r}")
    mods, keys = MOD_NOREPEAT, []
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif p in _KEYS:
            keys.append(_KEYS[p])
        else:
            raise ValueError(f"Unbekannte Taste: {p!r}")
    if len(keys) != 1:
        raise ValueError(f"Genau eine Taste nötig: {s!r}")
    return mods, keys[0]


def dispatch(name: str, cfg: Config) -> tuple[str, tuple] | None:
    """Übersetzt ein Tastenkürzel in eine Worker-Aktion für den Ziel-Raum."""
    target = cfg.target_room
    if not target:
        raise NoTarget("Kein Ziel-Raum gewählt")
    if name == "volume_up":
        return "change_group_volume", (target, cfg.volume_step)
    if name == "volume_down":
        return "change_group_volume", (target, -cfg.volume_step)
    if name == "play_pause":
        return "toggle_play", (target,)
    if name == "standby":
        return "standby_group", (target,)
    if name.startswith("favorite_"):
        idx = int(name.removeprefix("favorite_")) - 1
        if 0 <= idx < len(cfg.favorites):
            return "play_favorite", (target, cfg.favorites[idx])
        return None
    return None


class HotkeyManager(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey: Callable[[str], None]):
        super().__init__()
        self.on_hotkey = on_hotkey
        self._ids: dict[int, str] = {}
        self._user32 = ctypes.windll.user32 if sys.platform == "win32" else None

    def register(self, cfg: Config) -> list[str]:
        """Registriert alle Kürzel; liefert die Namen, die nicht registriert werden konnten."""
        self.unregister_all()
        failed: list[str] = []
        if self._user32 is None:
            return list(cfg.hotkeys)
        for i, (name, seq) in enumerate(sorted(cfg.hotkeys.items()), start=1):
            if not seq:
                continue
            try:
                mods, vk = parse_hotkey(seq)
            except ValueError:
                failed.append(name)
                continue
            if self._user32.RegisterHotKey(None, i, mods, vk):
                self._ids[i] = name
            else:
                failed.append(name)
        return failed

    def unregister_all(self) -> None:
        if self._user32 is not None:
            for i in self._ids:
                self._user32.UnregisterHotKey(None, i)
        self._ids.clear()

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self._ids:
                self.on_hotkey(self._ids[msg.wParam])
                return True, 0
        return False, 0
