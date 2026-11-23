"""Fassade über die Raumfeld-Anlage: Zustand lesen, Aktionen ausführen."""

from __future__ import annotations

import ipaddress
import random
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from ..i18n import tr
from ..raumfeld import favorites as rf
from ..raumfeld.didl import didl_for_stream, item_id_from_didl, title_from_didl, track_from_didl
from ..raumfeld.host import RaumfeldHost
from ..raumfeld.media import MediaServer, with_start_index
from ..raumfeld.models import MEDIA_RENDERER, MEDIA_SERVER, Device, Room, ZoneConfig
from ..raumfeld.upnp import AVT, RC, UpnpDevice, UpnpError
from .config import ANLAGE, Favorite
from .radio import is_hls
from .text import clean_text

POLL_INTERVAL = 0.5
ZONE_WAIT_STEPS = 10  # 5 s
PLAY_WAIT_STEPS = 16  # 8 s
WAKE_WAIT_STEPS = 40  # 20 s

# Raumfeld-Wiedergabemodi ↔ (Zufall, Wiederholen); RANDOM = Zufall + alle wiederholen.
# Zufall + einen wiederholen gibt es nicht – "einen" hat Vorrang.
_PLAY_MODES: dict[str, tuple[bool, str]] = {
    "NORMAL": (False, "off"),
    "SHUFFLE": (True, "off"),
    "REPEAT_ALL": (False, "all"),
    "RANDOM": (True, "all"),
    "REPEAT_ONE": (False, "one"),
}


def parse_play_mode(mode: str | None) -> tuple[bool, str]:
    return _PLAY_MODES.get(mode or "", (False, "off"))


def play_mode_for(shuffle: bool, repeat: str) -> str:
    if repeat == "one":
        return "REPEAT_ONE"
    if shuffle:
        return "RANDOM" if repeat == "all" else "SHUFFLE"
    return "REPEAT_ALL" if repeat == "all" else "NORMAL"


@dataclass
class RoomState:
    udn: str
    name: str
    zone_udn: str | None
    power: str
    playing: bool
    title: str | None
    volume: int | None
    spotify: bool
    track: str | None = None
    artist: str | None = None
    art_url: str | None = None
    is_queue: bool = False
    shuffle: bool = False
    repeat: str = "off"  # "off" | "all" | "one"
    can_toggle: bool = False  # Raum ohne Zone, der selbst Spotify spielt: Play/Pause direkt am Lautsprecher
    track_id: str | None = None  # Musik-Wiedergabeliste: Bibliotheks-ID des laufenden Titels


# Lautsprecher ohne Zone: nur aus diesen Zuständen heraus Play/Pause senden
_SPEAKER_TOGGLE_STATES = ("PLAYING", "TRANSITIONING", "PAUSED_PLAYBACK")


class PlayError(Exception):
    pass


class StartError(PlayError):
    """Anlage war bereit, aber die Quelle ließ sich nicht laden/starten (z. B. Album nicht mehr da)."""


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def anlage_favorite(server_udn: str, af: rf.AnlageFavorite) -> Favorite | None:
    """Eintrag der Teufel-Favoriten als DeskRemote-Favorit (ohne Ziel-Objekt nicht verwendbar)."""
    if not af.ref_id:
        return None
    station = af.kind == "station"
    name = clean_text(af.title, 200) or af.ref_id
    return Favorite(name, rf.play_uri(server_udn, af), (af.art_url or "") if station else "", ANLAGE, "",
                    None if station else af.art_url, 0, af.id, af.ref_id, af.kind)


def station_favorite(server_udn: str, hit: rf.StationHit) -> Favorite:
    """Suchtreffer zum direkten Abspielen (kein Eintrag in den Teufel-Favoriten)."""
    return Favorite(clean_text(hit.title, 200), rf.playsingle_uri(server_udn, hit.id), hit.art_url or "",
                    ANLAGE, "", None, 0, "", hit.id, "station")


class Anlage:
    def __init__(self, host: RaumfeldHost, device_factory: Callable[[str], UpnpDevice],
                 sleep: Callable[[float], None] = time.sleep,
                 rng: Callable[[int], int] = random.randrange):
        self.host = host
        self.device_factory = device_factory
        self.sleep = sleep
        self.rng = rng
        self._devices: dict[str, Device] = {}
        self._upnp: dict[str, tuple[str, UpnpDevice]] = {}
        self._server: tuple[str, MediaServer] | None = None

    def _location_ok(self, location: str) -> bool:
        parts = urlsplit(location)
        if parts.scheme != "http" or not parts.hostname:
            return False
        if parts.hostname == urlsplit(getattr(self.host, "base_url", "")).hostname:
            return True
        try:
            ip = ipaddress.ip_address(parts.hostname)
        except ValueError:
            return False
        return ip.is_private and not ip.is_loopback

    # --- Geräte ---------------------------------------------------------
    def _renderer(self, udn: str) -> UpnpDevice:
        if udn not in self._devices:
            self._devices = self.host.list_devices()
        dev = self._devices.get(udn)
        if dev is None or dev.type != MEDIA_RENDERER:
            raise UpnpError(tr("Gerät {udn} nicht gefunden", udn=udn))
        if not self._location_ok(dev.location):
            raise UpnpError(tr("Gerät {udn} meldet eine Adresse außerhalb des Heimnetzes", udn=udn))
        cached = self._upnp.get(udn)
        if cached is None or cached[0] != dev.location:
            cached = (dev.location, self.device_factory(dev.location))
            self._upnp[udn] = cached
        return cached[1]

    def _speaker(self, room: Room) -> UpnpDevice:
        if not room.renderer_udns:
            raise UpnpError(tr("Raum {room} hat keinen Lautsprecher", room=room.name))
        return self._renderer(room.renderer_udns[0])

    def _fresh(self, room_udn: str) -> tuple[ZoneConfig, Room]:
        zc, _ = self.host.get_zones()
        room = zc.room_by_udn(room_udn)
        if room is None:
            raise PlayError(tr("Raum nicht gefunden"))
        return zc, room

    # --- Zustand --------------------------------------------------------
    def refresh(self, zones: ZoneConfig | None = None) -> list[RoomState]:
        zc = zones if zones is not None else self.host.get_zones()[0]
        self._devices = {}
        states: list[RoomState] = []
        for zone in zc.zones:
            playing, title = False, None
            queue: dict = {}
            try:
                dev = self._renderer(zone.udn)
                state = dev.call(AVT, "GetTransportInfo", InstanceID=0).get("CurrentTransportState")
                playing = state in ("PLAYING", "TRANSITIONING")  # Raumfeld meldet nach Play ~3 s TRANSITIONING
                media = dev.call(AVT, "GetMediaInfo", InstanceID=0)
                title = title_from_didl(media.get("CurrentURIMetaData", ""))
                if media.get("CurrentURI", "").startswith("dlna-playcontainer://"):
                    try:
                        queue = self._queue_state(dev)
                    except UpnpError:
                        queue = {"is_queue": True}  # Titeldaten fehlen kurz – Zone bleibt sichtbar
                elif zone.spotify_active:
                    try:  # Spotify-Mehrraum-Modus: Titel steht nur am laufenden Track
                        track, artist, art = track_from_didl(
                            dev.call(AVT, "GetPositionInfo", InstanceID=0).get("TrackMetaData", ""))
                        queue = {"track": track, "artist": artist, "art_url": art}
                    except UpnpError:
                        pass  # dann eben nur „Spotify“
            except UpnpError:
                dev = None
            for room in zone.rooms:
                volume = None
                if dev is not None:
                    try:
                        volume = _int(dev.call(RC, "GetRoomVolume", InstanceID=0, Room=room.udn).get("CurrentVolume"))
                    except UpnpError:
                        pass
                states.append(RoomState(room.udn, room.name, zone.udn, room.power_state,
                                        # in einer Zone zählt nur die Zone: die Anlage lässt spotifyConnect
                                        # am Lautsprecher nach Spotify-Ende stehen
                                        playing, title, volume, zone.spotify_active, **queue))
        for room in zc.unassigned:
            volume = None
            try:
                volume = _int(self._speaker(room).call(RC, "GetVolume", InstanceID=0, Channel="Master").get("CurrentVolume"))
            except UpnpError:
                pass
            playing, extra = self._own_spotify(room)
            states.append(RoomState(room.udn, room.name, None, room.power_state, playing, None, volume,
                                    room.spotify_active, **extra))
        return sorted(states, key=lambda s: s.name.lower())

    def _own_spotify(self, room: Room) -> tuple[bool, dict]:
        """Einzelraum-Modus: Spotify spielt am Lautsprecher selbst, ohne Zone.

        Nur wache Räume mit Spotify-Markierung zählen – die Markierung bleibt nach
        Spotify-Ende stehen, ein Raum im Standby spielt sicher nichts.
        """
        if not room.spotify_active or room.power_state.endswith("STANDBY"):
            return False, {}
        try:
            spk = self._speaker(room)
            state = spk.call(AVT, "GetTransportInfo", InstanceID=0).get("CurrentTransportState")
        except UpnpError:
            return False, {}
        if state not in _SPEAKER_TOGGLE_STATES:
            return False, {}
        extra: dict = {"can_toggle": True}
        try:
            track, artist, art = track_from_didl(spk.call(AVT, "GetPositionInfo", InstanceID=0).get("TrackMetaData", ""))
            extra.update(track=track, artist=artist, art_url=art)
        except UpnpError:
            pass  # dann eben nur „Spotify“
        return state in ("PLAYING", "TRANSITIONING"), extra

    def _queue_state(self, dev: UpnpDevice) -> dict:
        """Zusatzfelder, wenn ein Album/Ordner läuft (Raumfeld-Wiedergabeliste)."""
        meta = dev.call(AVT, "GetPositionInfo", InstanceID=0).get("TrackMetaData", "")
        track, artist, art = track_from_didl(meta)
        shuffle, repeat = parse_play_mode(dev.call(AVT, "GetTransportSettings", InstanceID=0).get("PlayMode"))
        return {"track": track, "artist": artist, "art_url": art, "is_queue": True,
                "shuffle": shuffle, "repeat": repeat, "track_id": item_id_from_didl(meta)}

    # --- Aktionen -------------------------------------------------------
    def _ensure_zone(self, zc: ZoneConfig, room: Room):
        """Zone des Raums; hat er keine, wird eine angelegt und abgewartet, bis ihr Renderer da ist."""
        zone = zc.zone_of(room.udn)
        if zone is not None:
            return zone
        self.host.connect_rooms_to_zone([room.udn])
        for _ in range(ZONE_WAIT_STEPS):
            zone = self.host.get_zones()[0].zone_of(room.udn)
            if zone is not None:
                try:
                    self._renderer(zone.udn)  # Zonen-Renderer erscheint evtl. später in listDevices
                    return zone
                except UpnpError:
                    pass
            self.sleep(POLL_INTERVAL)
        raise PlayError(tr("{room} konnte nicht vorbereitet werden", room=room.name))

    def _wake_zone_confirmed(self, room_udn: str):
        """Alle Räume der Zone aus Standby holen und warten, bis die Anlage ACTIVE meldet.

        Lehre aus der Abnahme: nie in einen Zustandswechsel hineinfunken – erst bestätigen, dann laden.
        """
        zc, _ = self.host.get_zones()
        zone = zc.zone_of(room_udn)
        if zone is None:
            raise PlayError(tr("Die Zone ist nicht mehr da – bitte erneut versuchen"))
        sleepers = [r for r in zone.rooms if r.power_state.endswith("STANDBY")]
        if not sleepers:
            return zone
        dev = self._renderer(zone.udn)
        for r in sleepers:
            dev.call(AVT, "LeaveStandby", InstanceID=0, Room=r.udn)
        for _ in range(WAKE_WAIT_STEPS):
            zc, _ = self.host.get_zones()
            zone = zc.zone_of(room_udn)
            if zone is not None and not any(r.power_state.endswith("STANDBY") for r in zone.rooms):
                return zone
            self.sleep(POLL_INTERVAL)
        raise PlayError(tr("Die Lautsprecher wachen nicht auf – bitte kurz warten und erneut versuchen"))

    def _prepare_zone(self, room_udn: str):
        """Raum (bzw. Gruppe) wecken, Zone sicherstellen und erst zurückkehren, wenn alles ACTIVE ist."""
        zc, room = self._fresh(room_udn)
        if zc.zone_of(room_udn) is None and room.power_state.endswith("STANDBY"):
            # ohne Zone: erst den einzelnen Raum über seinen Lautsprecher wecken und bestätigen
            self._speaker(room).call(AVT, "LeaveStandby", InstanceID=0)
            for _ in range(WAKE_WAIT_STEPS):
                zc, room = self._fresh(room_udn)
                if not room.power_state.endswith("STANDBY"):
                    break
                self.sleep(POLL_INTERVAL)
            else:
                raise PlayError(tr("Die Lautsprecher wachen nicht auf – bitte kurz warten und erneut versuchen"))
        self._ensure_zone(zc, room)
        return self._wake_zone_confirmed(room_udn)

    def _play_confirmed(self, dev: UpnpDevice, uri: str, metadata: str) -> bool:
        dev.call(AVT, "SetAVTransportURI", InstanceID=0, CurrentURI=uri, CurrentURIMetaData=metadata)
        dev.call(AVT, "Play", InstanceID=0, Speed=1)
        for _ in range(PLAY_WAIT_STEPS):
            if dev.call(AVT, "GetTransportInfo", InstanceID=0).get("CurrentTransportState") == "PLAYING":
                return True
            self.sleep(POLL_INTERVAL)
        return False

    def play_container(self, room_udn: str, uri: str, metadata: str, shuffle: bool = False) -> None:
        """Album/Ordner über Raumfelds eigene Wiedergabeliste abspielen (synchron in Gruppen).

        shuffle: Zufallsmodus erst setzen, wenn die Wiedergabe bestätigt läuft (nie auf unklaren Zustand)."""
        zone = self._prepare_zone(room_udn)
        try:
            renderer = self._renderer(zone.udn)
            started = self._play_confirmed(renderer, uri, metadata)
        except UpnpError as exc:
            raise StartError(tr("Album konnte nicht gestartet werden ({error})", error=exc)) from exc
        if not started:
            raise StartError(tr("Diese Quelle kann Raumfeld nicht direkt abspielen"))
        if shuffle:
            try:
                renderer.call(AVT, "SetPlayMode", InstanceID=0, NewPlayMode=play_mode_for(True, "off"))
            except UpnpError as exc:
                raise PlayError(tr("Musik läuft, aber der Zufallsmodus ließ sich nicht einschalten")) from exc

    # --- Teufel-Favoriten ------------------------------------------------
    def media_server(self) -> MediaServer:
        """Der Medienserver der Anlage (dort liegen Bibliothek, TuneIn und die Teufel-Favoriten)."""
        devices = self._devices if any(d.type == MEDIA_SERVER for d in self._devices.values())             else self.host.list_devices()
        dev = next((d for d in devices.values() if d.type == MEDIA_SERVER and self._location_ok(d.location)), None)
        if dev is None:
            raise UpnpError(tr("Raumfeld-Medienserver nicht gefunden"))
        if self._server is None or self._server[0] != dev.location:
            self._server = (dev.location, MediaServer(dev.udn, dev.name, self.device_factory(dev.location),
                                                      raumfeld=True))
        return self._server[1]

    def favorites(self) -> list[Favorite]:
        server = self.media_server()
        return [f for af in rf.list_favorites(server) if (f := anlage_favorite(server.udn, af)) is not None]

    def add_favorite(self, object_id: str) -> list[Favorite]:
        """Objekt der Anlage (Sender, Album …) zu den Teufel-Favoriten; liefert die neue Liste."""
        rf.add_favorite(self.media_server(), object_id)
        return self.favorites()

    def remove_favorite(self, target: str) -> list[Favorite]:
        """Teufel-Favorit über sein Ziel-Objekt entfernen – der Eintrag wird frisch nachgeschlagen."""
        server = self.media_server()
        entry = rf.find_by_ref(rf.list_favorites(server), target)
        if entry is not None:
            rf.remove_favorite(server, entry.id)
        return self.favorites()

    def reorder_favorites(self, targets: list[str]) -> list[Favorite]:
        """Teufel-Favoriten in diese Reihenfolge bringen (wirkt auch in der Teufel-App)."""
        rf.reorder_favorites(self.media_server(), list(targets))
        return self.favorites()

    def search_stations(self, text: str) -> list[rf.StationHit]:
        return rf.search_stations(self.media_server(), text)

    def _play_anlage(self, room_udn: str, fav: Favorite) -> None:
        server = self.media_server()
        if fav.ref:  # Teufel-Favorit: aktuellen Eintrag nachschlagen (IDs wechseln beim Neuanlegen)
            entry = rf.find_by_ref(rf.list_favorites(server), fav.target)
            if entry is None:
                raise PlayError(tr("{name} steht nicht mehr in den Teufel-Favoriten", name=fav.name))
            uri, meta, shuffle = rf.play_uri(server.udn, entry), rf.favorite_metadata(server, entry), entry.kind == "artist"
        else:  # Suchtreffer: das Objekt selbst
            uri, meta, shuffle = rf.playsingle_uri(server.udn, fav.target), server.metadata(fav.target), False
        zone = self._prepare_zone(room_udn)
        try:
            renderer = self._renderer(zone.udn)
            started = self._play_confirmed(renderer, uri, meta)
        except UpnpError as exc:
            raise PlayError(tr("{name} konnte nicht starten ({error})", name=fav.name, error=exc)) from exc
        if not started:
            raise PlayError(tr("{name} konnte nicht starten", name=fav.name))
        if shuffle:
            try:
                renderer.call(AVT, "SetPlayMode", InstanceID=0, NewPlayMode=play_mode_for(True, "off"))
            except UpnpError as exc:
                raise PlayError(tr("Musik läuft, aber der Zufallsmodus ließ sich nicht einschalten")) from exc

    def play_favorite(self, room_udn: str, fav: Favorite) -> None:
        """Ein Favorit, egal welcher Art: Teufel-Favorit wie die Teufel-App, Sender als Stream,
        Musik über Raumfelds Wiedergabeliste."""
        if fav.on_anlage:
            self._play_anlage(room_udn, fav)
            return
        if not fav.is_music:
            self.play_stream(room_udn, fav)
            return
        uri = fav.url
        if fav.kind == "artist" and fav.count > 0:
            uri = with_start_index(uri, self.rng(fav.count))  # sonst immer dasselbe erste Lied
        try:
            self.play_container(room_udn, uri, fav.meta, shuffle=fav.kind == "artist")
        except StartError as exc:
            raise PlayError(tr("{name} konnte nicht starten – noch in der Bibliothek?", name=fav.name)) from exc

    def _zone_renderer(self, room_udn: str) -> UpnpDevice:
        zc, room = self._fresh(room_udn)
        zone = zc.zone_of(room_udn)
        if zone is None:
            raise PlayError(tr("In {room} läuft nichts", room=room.name))
        return self._renderer(zone.udn)

    def next_track(self, room_udn: str) -> None:
        self._zone_renderer(room_udn).call(AVT, "Next", InstanceID=0)

    def previous_track(self, room_udn: str) -> None:
        self._zone_renderer(room_udn).call(AVT, "Previous", InstanceID=0)

    def set_play_mode(self, room_udn: str, shuffle: bool, repeat: str) -> None:
        self._zone_renderer(room_udn).call(AVT, "SetPlayMode", InstanceID=0,
                                           NewPlayMode=play_mode_for(shuffle, repeat))

    def play_stream(self, room_udn: str, fav: Favorite) -> None:
        if is_hls(fav.url):  # nie an die Anlage: HLS ließ den Zonen-Renderer der One M neu starten
            raise PlayError(tr("{name} spielt auf Raumfeld nicht (HLS-Stream)", name=fav.name))
        zone = self._prepare_zone(room_udn)  # erst bestätigt wach, dann laden (sonst verpufft Play)
        try:
            if self._play_confirmed(self._renderer(zone.udn), fav.url, didl_for_stream(fav.name, fav.url)):
                return
        except UpnpError as exc:
            raise PlayError(tr("Sender nicht erreichbar ({error})", error=exc)) from exc
        raise PlayError(tr("Sender nicht erreichbar: {name}", name=fav.name))

    def toggle_play(self, room_udn: str) -> None:
        zc, room = self._fresh(room_udn)
        zone = zc.zone_of(room_udn)
        if zone is not None:
            dev = self._renderer(zone.udn)
        elif room.spotify_active and not room.power_state.endswith("STANDBY"):
            dev = self._speaker(room)  # Einzelraum-Spotify: der Lautsprecher selbst kann Play/Pause
        else:
            raise PlayError(tr("In {room} läuft nichts", room=room.name))
        state = dev.call(AVT, "GetTransportInfo", InstanceID=0).get("CurrentTransportState")
        if zone is None and state not in _SPEAKER_TOGGLE_STATES:
            raise PlayError(tr("In {room} läuft nichts", room=room.name))  # nie auf unbestätigten Zustand senden
        if state == "PLAYING":
            dev.call(AVT, "Pause", InstanceID=0)
        else:
            dev.call(AVT, "Play", InstanceID=0, Speed=1)

    def _get_volume(self, zc: ZoneConfig, room: Room) -> int:
        zone = zc.zone_of(room.udn)
        if zone is not None:
            out = self._renderer(zone.udn).call(RC, "GetRoomVolume", InstanceID=0, Room=room.udn)
        else:
            out = self._speaker(room).call(RC, "GetVolume", InstanceID=0, Channel="Master")
        volume = _int(out.get("CurrentVolume"))
        if volume is None:
            raise UpnpError(tr("Lautstärke von {room} nicht lesbar", room=room.name))
        return volume

    def _set_volume(self, zc: ZoneConfig, room: Room, volume: int) -> None:
        volume = max(0, min(100, int(volume)))
        zone = zc.zone_of(room.udn)
        if zone is not None:
            self._renderer(zone.udn).call(RC, "SetRoomVolume", InstanceID=0, Room=room.udn, DesiredVolume=volume)
        else:
            self._speaker(room).call(RC, "SetVolume", InstanceID=0, Channel="Master", DesiredVolume=volume)

    def set_volume(self, room_udn: str, volume: int) -> None:
        zc, room = self._fresh(room_udn)
        self._set_volume(zc, room, volume)

    def change_volume(self, room_udn: str, delta: int) -> None:
        zc, room = self._fresh(room_udn)
        self._set_volume(zc, room, self._get_volume(zc, room) + delta)

    def _standby_action(self, room_udn: str, action: str) -> None:
        zc, room = self._fresh(room_udn)
        zone = zc.zone_of(room_udn)
        if zone is not None:
            self._renderer(zone.udn).call(AVT, action, InstanceID=0, Room=room_udn)
        else:
            self._speaker(room).call(AVT, action, InstanceID=0)

    def _group_rooms(self, zc: ZoneConfig, room: Room) -> list[Room]:
        zone = zc.zone_of(room.udn)
        return list(zone.rooms) if zone is not None else [room]

    def standby_group(self, room_udn: str) -> None:
        """Ziel ist ein gekoppelter Raum → die ganze Gruppe geht in Standby."""
        zc, room = self._fresh(room_udn)
        for member in self._group_rooms(zc, room):
            self.standby(member.udn)

    def change_group_volume(self, room_udn: str, delta: int) -> None:
        """Alle Räume der Gruppe gleich viel lauter/leiser – das Verhältnis bleibt."""
        zc, room = self._fresh(room_udn)
        for member in self._group_rooms(zc, room):
            self._set_volume(zc, member, self._get_volume(zc, member) + delta)

    def standby(self, room_udn: str) -> None:
        self._standby_action(room_udn, "EnterManualStandby")

    def wake(self, room_udn: str) -> None:
        self._standby_action(room_udn, "LeaveStandby")

    def pause_all(self) -> None:
        zc, _ = self.host.get_zones()
        for zone in zc.zones:
            try:
                dev = self._renderer(zone.udn)
                if dev.call(AVT, "GetTransportInfo", InstanceID=0).get("CurrentTransportState") == "PLAYING":
                    dev.call(AVT, "Pause", InstanceID=0)
            except UpnpError:
                continue

    def couple(self, room_udns: list[str]) -> None:
        zc, _ = self.host.get_zones()
        zones = [z for u in room_udns if (z := zc.zone_of(u)) is not None]
        # Spotify-Zone zuerst: sonst verliert die Anlage beim Koppeln das Spotify-Gerät
        zone = next((z for z in zones if z.spotify_zone), zones[0] if zones else None)
        zone_udn = zone.udn if zone is not None else None
        self.host.connect_rooms_to_zone(room_udns, zone_udn)

    def uncouple(self, room_udn: str) -> None:
        self.host.drop_room(room_udn)
