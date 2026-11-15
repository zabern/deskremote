"""Protokoll aller Befehle an die Anlage – zum Nachvollziehen, wenn etwas schiefgeht."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
import re
from pathlib import Path

from .core.text import clean_text

LOGGER_NAME = "deskremote"
# Reine Statusabfragen fluten das Protokoll und sagen nichts darüber, was die App ausgelöst hat
QUIET_ACTIONS = {
    "GetTransportInfo", "GetMediaInfo", "GetPositionInfo", "GetRoomVolume", "GetVolume",
    "getZones", "listDevices",
}


def default_log_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "DeskRemote" / "deskremote.log"


def setup_logging(path: Path | None = None) -> Path:
    path = path or default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    handler = RotatingFileHandler(path, maxBytes=500_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return path


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


# gierig bis zum letzten "@" vor dem ersten "/" – Passwörter dürfen "@", "?" oder "#" enthalten
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s]*@")


def short(value: object, limit: int = 120) -> str:
    """Für das Protokoll: Steuerzeichen raus (keine gefälschten Zeilen), Zugangsdaten aus URLs raus, kürzen."""
    text = clean_text(str(value))  # erst bereinigen, dann Zugangsdaten entfernen, dann kürzen
    text = _USERINFO.sub(r"\1", text)
    return clean_text(text, limit)
