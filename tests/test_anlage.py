import pytest

from deskremote.core.anlage import Anlage, PlayError
from deskremote.core.config import Favorite
from deskremote.raumfeld.models import Device, Room, Zone, ZoneConfig
from deskremote.raumfeld.upnp import AVT, RC, UpnpError

FM4 = Favorite("FM4", "https://orf-live.ors-shoutcast.at/fm4-q2a")
WOHN, BAD, KUECHE = "uuid:wohn", "uuid:bad", "uuid:kueche"
ZBAD, ZKUECHE = "uuid:zbad", "uuid:zkueche"


def room(udn, name, power="ACTIVE", spotify=False):
    return Room(udn, name, power, [f"{udn}-spk"], spotify)


def config(bad_power="ACTIVE", zbad=ZBAD, wohn_zone=None):
    zones = [Zone(zbad, [room(BAD, "Badezimmer", bad_power)]), Zone(ZKUECHE, [room(KUECHE, "Küche")])]
    unassigned = [room(WOHN, "Wohnzimmer", spotify=True)]
    if wohn_zone:
        zones.append(Zone(wohn_zone, [room(WOHN, "Wohnzimmer")]))
        unassigned = []
    return ZoneConfig(zones, unassigned)


class FakeHost:
    base_url = "http://h:47365"

    def __init__(self, configs):
        self.configs = list(configs)
        self.calls = []

    def get_zones(self, update_id=None):
        cfg = self.configs[0] if len(self.configs) == 1 else self.configs.pop(0)
        return cfg, "1"

    def list_devices(self):
        udns = [ZBAD, ZKUECHE, "uuid:zneu", "uuid:zbad2", f"{BAD}-spk", f"{WOHN}-spk", f"{KUECHE}-spk"]
        return {u: Device(u, "urn:schemas-upnp-org:device:MediaRenderer:1", f"http://h/{u}.xml", u) for u in udns}

    def connect_rooms_to_zone(self, room_udns, zone_udn=None):
        self.calls.append(("connect", tuple(room_udns), zone_udn))

    def drop_room(self, room_udn):
        self.calls.append(("drop", room_udn))


class FakeDevice:
    def __init__(self, log, name, states=("PLAYING",), fail=False):
        self.log, self.name, self.states, self.fail = log, name, list(states), fail
        self.volume = "30"

    def call(self, service, action, **args):
        if self.fail:
            raise UpnpError("weg")
        self.log.append((self.name, action, args))
        if action == "GetTransportInfo":
            st = self.states[0] if len(self.states) == 1 else self.states.pop(0)
            return {"CurrentTransportState": st}
        if action in ("GetRoomVolume", "GetVolume"):
            return {"CurrentVolume": self.volume}
        if action == "GetMediaInfo":
            return {"CurrentURIMetaData": "<DIDL-Lite xmlns:dc='http://purl.org/dc/elements/1.1/'><item><dc:title>FM4</dc:title></item></DIDL-Lite>"}
        return {}


def make(configs, states=("PLAYING",), failing=()):
    log = []
    host = FakeHost(configs)
    devices = {}

    def factory(location):
        name = location.removeprefix("http://h/").removesuffix(".xml")
        if name not in devices:
            devices[name] = FakeDevice(log, name, states, fail=name in failing)
        return devices[name]

    a = Anlage(host, factory, sleep=lambda s: None)
    return a, host, log


def actions(log, name=None):
    return [(n, act) for n, act, _ in log if name is None or n == name]


def test_play_on_zoned_room_sets_uri_and_plays():
    a, host, log = make([config()])
    a.play_stream(BAD, FM4)
    acts = [act for n, act, _ in log if n == ZBAD]
    assert acts[:2] == ["SetAVTransportURI", "Play"]
    set_args = next(args for n, act, args in log if act == "SetAVTransportURI")
    assert set_args["CurrentURI"] == FM4.url
    assert "FM4" in set_args["CurrentURIMetaData"]


def test_play_on_unassigned_room_creates_zone_first():
    a, host, log = make([config(), config(), config(wohn_zone="uuid:zneu")])
    a.play_stream(WOHN, FM4)
    assert host.calls == [("connect", (WOHN,), None)]
    assert ("uuid:zneu", "SetAVTransportURI") in actions(log)


def test_play_unassigned_gives_up_when_zone_never_appears():
    a, host, log = make([config()])
    with pytest.raises(PlayError):
        a.play_stream(WOHN, FM4)


def test_play_wakes_room_in_standby():
    sleepy = config(bad_power="MANUAL_STANDBY")
    a, host, log = make([sleepy, sleepy, config()])
    a.play_stream(BAD, FM4)
    acts = actions(log, ZBAD)
    assert acts.index((ZBAD, "LeaveStandby")) < acts.index((ZBAD, "SetAVTransportURI"))
    leave = next(args for n, act, args in log if act == "LeaveStandby")
    assert leave["Room"] == BAD


def test_play_reports_failure_when_transport_never_plays():
    a, host, log = make([config()], states=("TRANSITIONING", "STOPPED"))
    with pytest.raises(PlayError, match="Sender nicht erreichbar"):
        a.play_stream(BAD, FM4)


def test_play_accepts_transitioning_then_playing():
    a, host, log = make([config()], states=("TRANSITIONING", "TRANSITIONING", "PLAYING"))
    a.play_stream(BAD, FM4)


def test_set_volume_uses_room_volume_in_zone():
    a, host, log = make([config()])
    a.set_volume(BAD, 40)
    _, act, args = log[-1]
    assert act == "SetRoomVolume" and args == {"InstanceID": 0, "Room": BAD, "DesiredVolume": 40}


def test_set_volume_clamps_to_0_100():
    a, host, log = make([config()])
    a.set_volume(BAD, 150)
    assert log[-1][2]["DesiredVolume"] == 100
    a.set_volume(BAD, -3)
    assert log[-1][2]["DesiredVolume"] == 0


def test_set_volume_unassigned_uses_speaker():
    a, host, log = make([config()])
    a.set_volume(WOHN, 20)
    name, act, args = log[-1]
    assert name == f"{WOHN}-spk" and act == "SetVolume" and args["Channel"] == "Master"


def test_change_volume_adds_delta():
    a, host, log = make([config()])
    a.change_volume(BAD, 5)
    assert log[-1][2]["DesiredVolume"] == 35


def test_standby_unassigned_uses_speaker_renderer():
    a, host, log = make([config()])
    a.standby(WOHN)
    assert log[-1][:2] == (f"{WOHN}-spk", "EnterManualStandby")
    assert log[-1][2] == {"InstanceID": 0}


def test_standby_in_zone_names_room():
    a, host, log = make([config()])
    a.standby(BAD)
    assert log[-1] == (ZBAD, "EnterManualStandby", {"InstanceID": 0, "Room": BAD})


def test_toggle_play_pauses_when_playing():
    a, host, log = make([config()], states=("PLAYING",))
    a.toggle_play(BAD)
    assert log[-1][1] == "Pause"


def test_toggle_play_plays_when_paused():
    a, host, log = make([config()], states=("PAUSED_PLAYBACK",))
    a.toggle_play(BAD)
    assert log[-1][1] == "Play"


def test_actions_resolve_zone_from_fresh_state():
    a, host, log = make([config(), config(zbad="uuid:zbad2")])
    a.set_volume(BAD, 10)
    a.set_volume(BAD, 11)
    assert [n for n, act, _ in log if act == "SetRoomVolume"] == [ZBAD, "uuid:zbad2"]


def test_couple_uses_existing_zone():
    a, host, log = make([config()])
    a.couple([WOHN, BAD])
    assert host.calls == [("connect", (WOHN, BAD), ZBAD)]


def test_uncouple_drops_room():
    a, host, log = make([config()])
    a.uncouple(BAD)
    assert host.calls == [("drop", BAD)]


def test_refresh_builds_states():
    a, host, log = make([config()])
    states = {s.udn: s for s in a.refresh()}
    assert states[BAD].zone_udn == ZBAD and states[BAD].playing and states[BAD].title == "FM4"
    assert states[BAD].volume == 30
    assert states[WOHN].zone_udn is None and states[WOHN].spotify is True


def test_refresh_tolerates_unreachable_room():
    a, host, log = make([config()], failing=(ZKUECHE,))
    states = {s.udn: s for s in a.refresh()}
    assert states[KUECHE].volume is None and states[KUECHE].title is None
    assert states[BAD].volume == 30


def test_renderer_with_foreign_location_is_refused():
    a, host, log = make([config()])
    devs = host.list_devices()
    devs[ZBAD] = Device(ZBAD, "urn:schemas-upnp-org:device:MediaRenderer:1", "http://8.8.8.8/x.xml", "x")
    host.list_devices = lambda: devs
    with pytest.raises(UpnpError):
        a._renderer(ZBAD)


def test_renderer_cache_follows_location_change():
    a, host, log = make([config()])
    first = a._renderer(ZBAD)
    devs = host.list_devices()
    devs[ZBAD] = Device(ZBAD, "urn:schemas-upnp-org:device:MediaRenderer:1", "http://h/uuid:zbad2.xml", "x")
    host.list_devices = lambda: devs
    a.refresh()
    assert a._renderer(ZBAD) is not first


def test_pause_all_pauses_only_playing_zones():
    a, host, log = make([config()], states=("PLAYING",))
    a.pause_all()
    assert sorted(n for n, act, _ in log if act == "Pause") == [ZBAD, ZKUECHE]


def test_play_waits_until_new_zone_renderer_is_listed():
    a, host, log = make([config(), config(), config(wohn_zone="uuid:zspaet")])
    base = host.list_devices
    seen = {"n": 0}

    def late_devices():
        seen["n"] += 1
        devs = base()
        if seen["n"] >= 3:
            devs["uuid:zspaet"] = Device("uuid:zspaet", "urn:schemas-upnp-org:device:MediaRenderer:1",
                                         "http://h/uuid:zspaet.xml", "x")
        return devs

    host.list_devices = late_devices
    a.play_stream(WOHN, FM4)
    assert ("uuid:zspaet", "SetAVTransportURI") in actions(log)


def test_change_volume_with_unreadable_volume_raises():
    a, host, log = make([config()])
    a._renderer(ZBAD).volume = ""
    with pytest.raises(UpnpError):
        a.change_volume(BAD, 5)
    assert not any(act == "SetRoomVolume" for _, act, _ in log)


def test_transitioning_counts_as_playing():
    a, host, log = make([config()], states=("TRANSITIONING",))
    states = {s.udn: s for s in a.refresh()}
    assert states[BAD].playing is True


def joint_config():
    return ZoneConfig([Zone(ZBAD, [room(BAD, "Badezimmer"), room(WOHN, "Wohnzimmer")]),
                       Zone(ZKUECHE, [room(KUECHE, "Küche")])], [])






def test_couple_never_touches_playback():
    """Abnahme 13.11.: Pause/Play direkt nach dem Koppeln brachte die One M zum Absturz."""
    a, host, log = make([config(), joint_config()], states=("PLAYING",))
    a.couple([WOHN, BAD])
    assert host.calls == [("connect", (WOHN, BAD), ZBAD)]
    assert not any(act in ("Pause", "Play", "Stop", "SetAVTransportURI") for _, act, _ in log)


def test_standby_group_puts_every_room_of_the_zone_to_standby():
    a, host, log = make([joint_config()])
    a.standby_group(BAD)
    rooms_sent = sorted(args["Room"] for n, act, args in log if act == "EnterManualStandby")
    assert rooms_sent == sorted([BAD, WOHN])


def test_standby_group_on_single_room_is_plain_standby():
    a, host, log = make([config()])
    a.standby_group(WOHN)
    assert [(n, act) for n, act, _ in log] == [(f"{WOHN}-spk", "EnterManualStandby")]


def test_change_group_volume_moves_every_room():
    a, host, log = make([joint_config()])
    a.change_group_volume(BAD, 5)
    sets = sorted((args["Room"], args["DesiredVolume"]) for n, act, args in log if act == "SetRoomVolume")
    assert sets == sorted([(BAD, 35), (WOHN, 35)])


# --- V2: Album-Wiedergabe ---------------------------------------------------
from deskremote.core.anlage import parse_play_mode, play_mode_for  # noqa: E402

URI = "dlna-playcontainer://uuid%3Ams?sid=x&cid=0%2Fa&md=0&fii=2"
META = "<DIDL-Lite><container id='0/a'/></DIDL-Lite>"


def standby_group(power="MANUAL_STANDBY"):
    return ZoneConfig([Zone(ZBAD, [room(BAD, "Badezimmer", power), room(WOHN, "Wohnzimmer", power)]),
                       Zone(ZKUECHE, [room(KUECHE, "Küche")])], [])


def test_play_container_wakes_group_and_waits_active():
    a, host, log = make([standby_group(), standby_group(), standby_group(), standby_group("ACTIVE")])
    a.play_container(BAD, URI, META)
    acts = [(act, args.get("Room")) for n, act, args in log if n == ZBAD]
    leave = [r for act, r in acts if act == "LeaveStandby"]
    assert sorted(leave) == sorted([BAD, WOHN])
    names = [act for act, _ in acts]
    assert names.index("SetAVTransportURI") > max(i for i, x in enumerate(names) if x == "LeaveStandby")
    assert names.index("Play") > names.index("SetAVTransportURI")
    set_args = next(args for n, act, args in log if act == "SetAVTransportURI")
    assert set_args == {"InstanceID": 0, "CurrentURI": URI, "CurrentURIMetaData": META}
    assert len(host.configs) == 1  # erst nach der ACTIVE-Bestätigung geladen


def test_play_container_gives_up_if_room_stays_standby():
    a, host, log = make([standby_group()])
    with pytest.raises(PlayError, match="wach"):
        a.play_container(BAD, URI, META)
    assert not any(act == "SetAVTransportURI" for _, act, _ in log)


def test_play_container_unassigned_creates_zone_first():
    a, host, log = make([config(), config(), config(wohn_zone="uuid:zneu")])
    a.play_container(WOHN, URI, META)
    assert host.calls == [("connect", (WOHN,), None)]
    assert ("uuid:zneu", "SetAVTransportURI") in actions(log)


def test_next_previous_use_zone_renderer():
    a, host, log = make([config()])
    a.next_track(BAD)
    a.previous_track(BAD)
    assert [(n, act) for n, act, _ in log] == [(ZBAD, "Next"), (ZBAD, "Previous")]


def test_next_without_zone_raises():
    a, host, log = make([config()])
    with pytest.raises(PlayError):
        a.next_track(WOHN)


def test_play_mode_table():
    table = {"NORMAL": (False, "off"), "SHUFFLE": (True, "off"), "REPEAT_ALL": (False, "all"),
             "RANDOM": (True, "all"), "REPEAT_ONE": (False, "one")}
    for mode, (shuffle, repeat) in table.items():
        assert parse_play_mode(mode) == (shuffle, repeat)
        assert play_mode_for(shuffle, repeat) == mode
    assert parse_play_mode("UNBEKANNT") == (False, "off")


def test_play_mode_repeat_one_wins():
    assert play_mode_for(True, "one") == "REPEAT_ONE"


def test_set_play_mode_sends_raumfeld_mode():
    a, host, log = make([config()])
    a.set_play_mode(BAD, True, "all")
    assert log[-1] == (ZBAD, "SetPlayMode", {"InstanceID": 0, "NewPlayMode": "RANDOM"})


class QueueDevice(FakeDevice):
    def call(self, service, action, **args):
        if action == "GetMediaInfo":
            self.log.append((self.name, action, args))
            return {"CurrentURI": URI, "CurrentURIMetaData": META, "NrTracks": "9"}
        if action == "GetPositionInfo":
            self.log.append((self.name, action, args))
            return {"Track": "3", "TrackMetaData": (
                "<DIDL-Lite xmlns:dc='http://purl.org/dc/elements/1.1/' "
                "xmlns:upnp='urn:schemas-upnp-org:metadata-1-0/upnp/'><item><dc:title>Titel 05</dc:title>"
                "<upnp:artist>Naked Lunch</upnp:artist><upnp:albumArtURI>http://192.168.0.176:47366/?a=1</upnp:albumArtURI>"
                "</item></DIDL-Lite>")}
        if action == "GetTransportSettings":
            self.log.append((self.name, action, args))
            return {"PlayMode": "REPEAT_ONE"}
        return super().call(service, action, **args)


def test_refresh_reads_track_and_mode_for_queue():
    log = []
    host = FakeHost([config()])
    a = Anlage(host, lambda loc: QueueDevice(log, loc.removeprefix("http://h/").removesuffix(".xml")),
               sleep=lambda s: None)
    s = {x.udn: x for x in a.refresh()}[BAD]
    assert s.is_queue is True and s.track == "Titel 05" and s.artist == "Naked Lunch"
    assert s.art_url == "http://192.168.0.176:47366/?a=1"
    assert (s.shuffle, s.repeat) == (False, "one")


def test_refresh_radio_has_no_queue_fields():
    a, host, log = make([config()])
    s = {x.udn: x for x in a.refresh()}[BAD]
    assert s.is_queue is False and s.track is None and s.repeat == "off"
    assert not any(act in ("GetPositionInfo", "GetTransportSettings") for _, act, _ in log)


def test_play_container_unassigned_standby_wakes_before_zone():
    sleepy = ZoneConfig([Zone(ZBAD, [room(BAD, "Badezimmer")]), Zone(ZKUECHE, [room(KUECHE, "Küche")])],
                        [room(WOHN, "Wohnzimmer", "MANUAL_STANDBY")])
    awake = config()
    a, host, log = make([sleepy, sleepy, awake, awake, config(wohn_zone="uuid:zneu")])
    a.play_container(WOHN, URI, META)
    leave_i = next(i for i, (n, act, _) in enumerate(log) if act == "LeaveStandby")
    assert log[leave_i][0] == f"{WOHN}-spk"
    assert host.calls == [("connect", (WOHN,), None)]
    assert ("uuid:zneu", "SetAVTransportURI") in actions(log)


def test_refresh_queue_error_keeps_zone_state():
    class Flaky(QueueDevice):
        def call(self, service, action, **args):
            if action == "GetPositionInfo":
                raise UpnpError("kurz weg")
            return super().call(service, action, **args)

    log = []
    a = Anlage(FakeHost([config()]), lambda loc: Flaky(log, loc.removeprefix("http://h/").removesuffix(".xml")),
               sleep=lambda s: None)
    s = {x.udn: x for x in a.refresh()}[BAD]
    assert s.volume == 30 and s.playing is True


def test_wake_on_vanished_zone_raises_play_error():
    gone = ZoneConfig([Zone(ZKUECHE, [room(KUECHE, "Küche")])], [room(BAD, "Badezimmer")])
    a, host, log = make([gone])
    with pytest.raises(PlayError):
        a._wake_zone_confirmed(BAD)


def test_play_container_shuffle_set_only_after_playing_confirmed():
    a, host, log = make([config(), config(), config(wohn_zone="uuid:zneu")])
    a.play_container(WOHN, URI, META, shuffle=True)
    names = [act for n, act, _ in log if n == "uuid:zneu"]
    assert names.index("SetPlayMode") > names.index("Play")
    assert names.index("SetPlayMode") > names.index("GetTransportInfo")
    mode = next(args for n, act, args in log if act == "SetPlayMode")
    assert mode["NewPlayMode"] == "SHUFFLE"


def test_play_container_without_shuffle_leaves_mode_alone():
    a, host, log = make([config(), config(), config(wohn_zone="uuid:zneu")])
    a.play_container(WOHN, URI, META)
    assert "SetPlayMode" not in [act for _, act, _ in log]



def test_play_stream_from_standby_waits_for_active_before_loading():
    """Abnahme v2: Sender aus Standby – Play kam 0,1 s nach LeaveStandby und verpuffte."""
    a, host, log = make([standby_group()] * 6 + [standby_group("ACTIVE")])
    a.play_stream(BAD, FM4)
    names = [act for n, act, _ in log if n == ZBAD]
    assert names.index("SetAVTransportURI") > max(i for i, x in enumerate(names) if x == "LeaveStandby")
    assert len(host.configs) == 1  # erst nach der ACTIVE-Bestätigung geladen


def test_play_stream_gives_up_if_room_stays_standby():
    a, host, log = make([standby_group()])
    with pytest.raises(PlayError, match="wach"):
        a.play_stream(BAD, FM4)
    assert not any(act == "SetAVTransportURI" for _, act, _ in log)



ALBUM_FAV = Favorite("Album 22 · Die Sterne", "dlna-playcontainer://uuid%3Ams?sid=x&cid=0%2Fa&md=0&fii=0",
                     kind="album", meta=META)
ARTIST_FAV = Favorite("Die Sterne · Zufall", "dlna-playcontainer://uuid%3Ams?sid=x&cid=0%2Fa%2FAllTracks&md=0&fii=0",
                      kind="artist", meta=META, count=10)


def _set_uri(log):
    return next(args for n, act, args in log if act == "SetAVTransportURI")["CurrentURI"]


def test_play_favorite_stream_uses_play_stream():
    a, host, log = make([config()])
    a.play_favorite(BAD, FM4)
    assert _set_uri(log) == FM4.url


def test_play_favorite_album_plays_container_without_shuffle():
    a, host, log = make([config()])
    a.play_favorite(BAD, ALBUM_FAV)
    assert _set_uri(log) == ALBUM_FAV.url
    assert next(args for n, act, args in log if act == "SetAVTransportURI")["CurrentURIMetaData"] == META
    assert "SetPlayMode" not in [act for _, act, _ in log]


def test_play_favorite_artist_random_start_and_shuffle():
    a, host, log = make([config()])
    seen = []
    a.rng = lambda n: seen.append(n) or 4
    a.play_favorite(BAD, ARTIST_FAV)
    assert seen == [10] and _set_uri(log).endswith("&fii=4")
    mode = next(args for n, act, args in log if act == "SetPlayMode")
    assert mode["NewPlayMode"] == "SHUFFLE"


def test_play_favorite_artist_without_count_starts_at_zero():
    from dataclasses import replace
    a, host, log = make([config()])
    a.rng = lambda n: pytest.fail("rng darf ohne Titelanzahl nicht laufen")
    a.play_favorite(BAD, replace(ARTIST_FAV, count=0))
    assert _set_uri(log).endswith("&fii=0")


def test_play_favorite_music_failure_message():
    a, host, log = make([config()], states=("STOPPED",))
    with pytest.raises(PlayError, match="Album 22 · Die Sterne konnte nicht starten – noch in der Bibliothek\\?"):
        a.play_favorite(BAD, ALBUM_FAV)


def test_play_favorite_music_never_sends_stream_path():
    a, host, log = make([config()])
    a.play_favorite(BAD, ALBUM_FAV)
    assert not any(act == "SetAVTransportURI" and args["CurrentURI"].startswith("http")
                   for _, act, args in log)


def test_play_favorite_wake_failure_keeps_wake_message():
    a, host, log = make([standby_group()])
    with pytest.raises(PlayError, match="wach"):
        a.play_favorite(BAD, ALBUM_FAV)



def test_shuffle_failure_is_not_reported_as_missing_album():
    """Fix 6: Musik läuft, nur der Zufallsmodus scheitert – keine Bibliothek-Vermutung."""
    a, host, log = make([config()])
    dev = a._renderer(ZBAD)
    orig = dev.call

    def call(service, action, **args):
        if action == "SetPlayMode":
            raise UpnpError("abgelehnt")
        return orig(service, action, **args)

    dev.call = call
    with pytest.raises(PlayError) as err:
        a.play_favorite(BAD, ARTIST_FAV)
    assert "Zufall" in str(err.value) and "Bibliothek" not in str(err.value)
    assert (ZBAD, "Play") in actions(log)



SPOTIFY_TRACK = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                 'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                 '<item id="0/Spotify/Track"><dc:title>Titel 10</dc:title><upnp:artist>Whitney</upnp:artist>'
                 '<upnp:albumArtURI>https://i.scdn.co/image/x</upnp:albumArtURI></item></DIDL-Lite>')


class SpotifyDevice(FakeDevice):
    def __init__(self, log, name, position_fails=False):
        super().__init__(log, name)
        self.position_fails = position_fails

    def call(self, service, action, **args):
        if action == "GetMediaInfo":
            return {"CurrentURI": "spotify-playlist://playback?cookie=1",
                    "CurrentURIMetaData": '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"><item/></DIDL-Lite>'}
        if action == "GetPositionInfo":
            if self.position_fails:
                raise UpnpError("kurz weg")
            return {"Track": "1", "TrackMetaData": SPOTIFY_TRACK}
        return super().call(service, action, **args)


def spotify_config(active=True):
    return ZoneConfig([Zone(ZBAD, [room(BAD, "Badezimmer"), room(KUECHE, "Küche")], spotify_active=active)],
                      [room(WOHN, "Wohnzimmer")])


def test_refresh_spotify_zone_fills_track():
    log = []
    a = Anlage(FakeHost([spotify_config()]), lambda loc: SpotifyDevice(log, loc), sleep=lambda s: None)
    s = {x.udn: x for x in a.refresh()}
    for udn in (BAD, KUECHE):
        assert s[udn].spotify is True
        assert (s[udn].track, s[udn].artist) == ("Titel 10", "Whitney")
        assert s[udn].is_queue is False
    assert s[WOHN].spotify is False


def test_refresh_spotify_zone_without_track_info():
    log = []
    a = Anlage(FakeHost([spotify_config()]), lambda loc: SpotifyDevice(log, loc, position_fails=True),
               sleep=lambda s: None)
    s = {x.udn: x for x in a.refresh()}[BAD]
    assert s.spotify is True and s.track is None and s.playing is True


def test_refresh_inactive_spotify_zone_is_not_spotify():
    log = []
    a = Anlage(FakeHost([spotify_config(active=False)]), lambda loc: SpotifyDevice(log, loc), sleep=lambda s: None)
    s = {x.udn: x for x in a.refresh()}[BAD]
    assert s.spotify is False and s.track is None



def test_stale_speaker_flag_in_zone_is_not_spotify():
    """Die Anlage lässt spotifyConnect am Lautsprecher stehen; in einer Zone zählt nur die Zone."""
    zc = ZoneConfig([Zone(ZBAD, [room(BAD, "Badezimmer", spotify=True)])], [room(WOHN, "Wohnzimmer", spotify=True)])
    a, host, log = make([zc])
    s = {x.udn: x for x in a.refresh()}
    assert s[BAD].spotify is False and s[BAD].title == "FM4"
    assert s[WOHN].spotify is True  # Einzelraum-Modus: Raum ohne Zone spielt Spotify



def test_couple_prefers_spotify_zone():
    """Räume aus zwei Gruppen koppeln: die Spotify-Zone bleibt erhalten, nicht die des ersten Raums."""
    zc = ZoneConfig([Zone(ZBAD, [room(BAD, "Badezimmer")]),
                     Zone(ZKUECHE, [room(KUECHE, "Küche")], spotify_zone=True)], [room(WOHN, "Wohnzimmer")])
    a, host, log = make([zc])
    a.couple([BAD, KUECHE, WOHN])
    assert host.calls == [("connect", (BAD, KUECHE, WOHN), ZKUECHE)]


def test_play_stream_refuses_hls_without_touching_device():
    """HLS ließ den Zonen-Renderer der One M neu starten – nie an die Anlage senden."""
    a, host, log = make([config()])
    bbc = Favorite("BBC 6 Music", "http://a.files.bbci.co.uk/x/bbc_6music.m3u8?s=1")
    with pytest.raises(PlayError, match="spielt auf Raumfeld nicht"):
        a.play_stream(BAD, bbc)
    assert log == [] and host.calls == []
