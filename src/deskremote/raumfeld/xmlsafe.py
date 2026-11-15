"""Gehärtetes XML-Parsen für alle Netzwerkantworten (keine DTDs/Entitäten, Größenlimit)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

MAX_BYTES = 2_000_000


class XmlError(ValueError):
    pass


def parse(text: str | bytes) -> ET.Element:
    data = text.encode("utf-8") if isinstance(text, str) else text
    if len(data) > MAX_BYTES:
        raise XmlError("XML-Antwort zu groß")
    try:
        return fromstring(data, forbid_dtd=True)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise XmlError(str(exc)) from exc
