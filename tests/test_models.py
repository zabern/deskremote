from pathlib import Path

from deskremote.raumfeld.models import (
    CONFIG_DEVICE,
    MEDIA_RENDERER,
    parse_devices,
    parse_zone_config,
)

FIX = Path(__file__).parent / "fixtures"
BAD = "uuid:211f83b8-264d-5140-825e-ab764eb6eb2e"
BAD_ZONE = "uuid:53c6005e-fe3b-5e5f-b1a0-17a8343aa163"
WOHN = "uuid:d61b4a9c-428f-5007-83b2-71c87fc44aba"


def zones():
    return parse_zone_config((FIX / "getZones.xml").read_text(encoding="utf-8"))


def test_parse_zones():
    zc = zones()
    assert len(zc.zones) == 2
    zone = next(z for z in zc.zones if z.udn == BAD_ZONE)
    assert zone.rooms[0].name == "Badezimmer"
    assert zone.rooms[0].power_state == "AUTOMATIC_STANDBY"
    assert zone.rooms[0].renderer_udns == ["uuid:29815994-847e-5cb7-b8a0-543c5bb4c052"]


def test_parse_unassigned():
    zc = zones()
    assert zc.unassigned[0].name == "Wohnzimmer"
    assert zc.unassigned[0].power_state == "ACTIVE"
    assert zc.unassigned[0].spotify_active is True
    assert zc.room_by_udn(BAD).spotify_active is False


def test_zone_of_and_room_lookup():
    zc = zones()
    assert zc.zone_of(BAD).udn == BAD_ZONE
    assert zc.zone_of(WOHN) is None
    assert zc.room_by_udn(WOHN).name == "Wohnzimmer"
    assert zc.room_by_udn("uuid:nix") is None
    assert {r.name for r in zc.all_rooms()} == {"Wohnzimmer", "Küche", "Badezimmer"}


def test_parse_devices():
    devs = parse_devices((FIX / "listDevices.xml").read_text(encoding="utf-8"))
    d = devs[BAD_ZONE]
    assert d.type == MEDIA_RENDERER
    assert d.name == "Badezimmer"
    assert d.location.startswith("http://192.168.0.176:")
    assert sum(1 for x in devs.values() if x.type == CONFIG_DEVICE) == 1


def test_parse_empty_zone_config():
    zc = parse_zone_config("<zoneConfig numRooms='0'><zones/></zoneConfig>")
    assert zc.zones == [] and zc.unassigned == []


def test_entity_in_zone_config_rejected():
    import pytest
    from deskremote.raumfeld.models import XmlError
    with pytest.raises(XmlError):
        parse_zone_config('<!DOCTYPE r [<!ENTITY a "x">]><zoneConfig>&a;</zoneConfig>')


def test_wrong_root_is_rejected():
    import pytest
    from deskremote.raumfeld.models import XmlError
    with pytest.raises(XmlError):
        parse_zone_config("<html>zoneConfig</html>")


def test_parse_zone_spotify_active():
    zc = parse_zone_config((FIX / "getZones-multiroom.xml").read_text(encoding="utf-8"))
    flags = {z.udn: z.spotify_active for z in zc.zones}
    assert flags == {"uuid:zone-spotify": True, "uuid:zone-bad": False}
    assert zones().zones[0].spotify_active is False  # Einzelraum-Modus: kein Zonen-Attribut


def test_parse_zone_spotify_zone_marker():
    zc = parse_zone_config((FIX / "getZones-multiroom.xml").read_text(encoding="utf-8"))
    assert all(z.spotify_zone for z in zc.zones)  # active und inactive: beide tragen die Markierung
    assert not any(z.spotify_zone for z in zones().zones)
