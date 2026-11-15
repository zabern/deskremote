from deskremote.raumfeld.didl import didl_for_stream, title_from_didl

REAL = (
    '<?xml version="1.0" encoding="utf-8" standalone="no"?>\n'
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:raumfeld="urn:schemas-raumfeld-com:meta-data/raumfeld" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" lang="en">'
    '<item id="0/Favorites/MostPlayed/248" parentID="0/Favorites/MostPlayed" '
    'refID="0/RadioTime/Search/s-s15586" restricted="1"><dc:title>kronehit</dc:title>'
    "<upnp:class>object.item.audioItem.audioBroadcast.radio</upnp:class></item></DIDL-Lite>"
)


def test_didl_roundtrip():
    didl = didl_for_stream("Ö1", "http://orf-live.ors-shoutcast.at/oe1-q2a")
    assert title_from_didl(didl) == "Ö1"
    assert "object.item.audioItem.audioBroadcast" in didl
    assert 'protocolInfo="http-get:*:audio/mpeg:*"' in didl


def test_didl_escapes():
    didl = didl_for_stream("A & B", "http://x/?a=1&b=2")
    assert title_from_didl(didl) == "A & B"
    assert "&amp;b=2" in didl


def test_title_from_real_didl():
    assert title_from_didl(REAL) == "kronehit"


def test_station_name_preferred():
    didl = REAL.replace(
        "<dc:title>kronehit</dc:title>",
        "<dc:title>Song</dc:title><upnp:stationName>KroneHit</upnp:stationName>",
    )
    assert title_from_didl(didl) == "KroneHit"


def test_broken_or_empty():
    assert title_from_didl("") is None
    assert title_from_didl("<kaputt") is None


def test_entity_in_didl_rejected():
    assert title_from_didl('<!DOCTYPE r [<!ENTITY a "x">]><r>&a;</r>') is None


def test_control_chars_are_removed():
    didl = didl_for_stream("A\x01B\x1fC", "http://x/\x02y")
    assert title_from_didl(didl) == "ABC"
    assert "\x02" not in didl


def test_track_from_didl_is_cleaned():
    from deskremote.raumfeld.didl import track_from_didl
    didl = ("<DIDL-Lite xmlns:dc='http://purl.org/dc/elements/1.1/' "
            "xmlns:upnp='urn:schemas-upnp-org:metadata-1-0/upnp/'><item>"
            "<dc:title>A&#x202E;B" + "x" * 400 + "</dc:title><upnp:artist>C&#x2028;D</upnp:artist></item></DIDL-Lite>")
    title, artist, art = track_from_didl(didl)
    assert "\u202e" not in title and len(title) <= 200 and artist == "CD" and art is None
