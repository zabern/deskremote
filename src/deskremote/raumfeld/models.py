"""Datenmodelle und Parser für die Antworten der Raumfeld-Host-API."""

from __future__ import annotations

from dataclasses import dataclass, field

from .xmlsafe import XmlError, parse  # noqa: F401  (XmlError re-exportiert)

MEDIA_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
MEDIA_SERVER = "urn:schemas-upnp-org:device:MediaServer:1"
CONFIG_DEVICE = "urn:schemas-raumfeld-com:device:ConfigDevice:1"


@dataclass
class Room:
    udn: str
    name: str
    power_state: str
    renderer_udns: list[str] = field(default_factory=list)
    spotify_active: bool = False


@dataclass
class Zone:
    udn: str
    rooms: list[Room]
    spotify_active: bool = False  # Spotify-Mehrraum-Modus: die Anlage meldet Spotify an der Zone
    spotify_zone: bool = False  # diese Zone ist das Spotify-Gerät (Markierung da, ob es gerade spielt oder nicht)


@dataclass
class ZoneConfig:
    zones: list[Zone]
    unassigned: list[Room]

    def all_rooms(self) -> list[Room]:
        return [r for z in self.zones for r in z.rooms] + list(self.unassigned)

    def room_by_udn(self, udn: str) -> Room | None:
        return next((r for r in self.all_rooms() if r.udn == udn), None)

    def zone_of(self, room_udn: str) -> Zone | None:
        return next((z for z in self.zones if any(r.udn == room_udn for r in z.rooms)), None)


@dataclass
class Device:
    udn: str
    type: str
    location: str
    name: str


def _room(el) -> Room:
    renderers = el.findall("renderer")
    return Room(
        udn=el.get("udn", ""),
        name=el.get("name", ""),
        power_state=el.get("powerState", ""),
        renderer_udns=[r.get("udn", "") for r in renderers],
        spotify_active=any(r.get("spotifyConnect") == "active" for r in renderers),
    )


def parse_zone_config(xml: str) -> ZoneConfig:
    root = parse(xml)
    if root.tag != "zoneConfig":
        raise XmlError(f"Keine Raumfeld-Zonenkonfiguration (Wurzel {root.tag})")
    zones = [
        Zone(udn=z.get("udn", ""), rooms=[_room(r) for r in z.findall("room")],
             spotify_active=z.get("spotifyConnect") == "active",
             spotify_zone=z.get("spotifyConnect") is not None)
        for z in root.findall("./zones/zone")
    ]
    unassigned = [_room(r) for r in root.findall("./unassignedRooms/room")]
    return ZoneConfig(zones=zones, unassigned=unassigned)


def parse_devices(xml: str) -> dict[str, Device]:
    root = parse(xml)
    if root.tag != "devices":
        raise XmlError(f"Keine Raumfeld-Geräteliste (Wurzel {root.tag})")
    return {
        d.get("udn", ""): Device(
            udn=d.get("udn", ""),
            type=d.get("type", ""),
            location=d.get("location", ""),
            name=(d.text or "").strip(),
        )
        for d in root.findall("device")
    }
