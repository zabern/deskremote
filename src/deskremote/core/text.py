"""Bereinigung von Texten aus fremden Quellen (Senderverzeichnis, Lautsprecher, Config)."""

from __future__ import annotations

import unicodedata


def clean_text(value: object, max_len: int | None = None) -> str:
    """Entfernt Steuer- und Formatzeichen (z. B. Bidi-Overrides), trimmt und kürzt."""
    text = "".join(ch for ch in str(value) if unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp"))
    text = text.strip()
    if max_len is not None and len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text
