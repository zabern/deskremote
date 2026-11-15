import threading
import time

import httpx
import pytest

from deskremote.core.anlage import PlayError, RoomState
from deskremote.core.config import Favorite, default_config
from deskremote.ui.worker import Worker

STATE = [RoomState("uuid:bad", "Badezimmer", "uuid:z", "ACTIVE", False, None, 30, False)]


class FakeHost:
    def __init__(self):
        self.fail = False

    def get_zones(self, update_id=None):
        if self.fail:
            raise httpx.ConnectError("weg")
        if update_id is not None:
            time.sleep(0.2)
        return None, "1"


class FakeAnlage:
    def __init__(self, host):
        self.host = host
        self.calls = []
        self.lock = threading.Lock()

    def refresh(self, zones=None):
        self.host.get_zones()
        return STATE

    def _rec(self, *c):
        with self.lock:
            self.calls.append(c)

    def play_stream(self, room, fav):
        self._rec("play_stream", room, fav)
        if fav.name == "tot":
            raise PlayError("Sender nicht erreichbar: tot")

    def set_volume(self, room, v):
        self._rec("set_volume", room, v)


@pytest.fixture
def setup(tmp_path):
    host = FakeHost()
    anlage = FakeAnlage(host)
    saved = []
    found = {"n": 0}

    def discover(last):
        found["n"] += 1
        return None if found.get("offline") else "http://h:47365"

    w = Worker(default_config(), make_anlage=lambda url: anlage, discover=discover,
               save=saved.append, poll_interval=0.2, retry_interval=0.1)
    yield w, anlage, host, saved, found
    w.stop()


def test_submit_runs_action_and_emits_state(qtbot, setup):
    w, anlage, host, saved, found = setup
    with qtbot.waitSignal(w.online_changed, timeout=3000):
        w.start()
    with qtbot.waitSignal(w.state_changed, timeout=3000) as blocker:
        w.submit("play_stream", "uuid:bad", Favorite("FM4", "u"))
    assert blocker.args[0] == STATE
    assert ("play_stream", "uuid:bad", Favorite("FM4", "u")) in anlage.calls
    assert saved and saved[-1].last_host == "http://h:47365"


def test_worker_reports_offline_then_recovers(qtbot, setup):
    w, anlage, host, saved, found = setup
    events = []
    w.online_changed.connect(events.append)
    found["offline"] = True
    w.start()
    qtbot.waitUntil(lambda: found["n"] >= 2, timeout=3000)
    assert True not in events
    found["offline"] = False
    qtbot.waitUntil(lambda: True in events, timeout=3000)
    host.fail = True
    qtbot.waitUntil(lambda: events[-1] is False, timeout=3000)
    host.fail = False
    qtbot.waitUntil(lambda: events[-1] is True, timeout=3000)


def test_volume_calls_are_coalesced(qtbot, setup):
    w, anlage, host, saved, found = setup
    with qtbot.waitSignal(w.online_changed, timeout=3000):
        w.start()
    for v in range(10, 20):
        w.submit("set_volume", "uuid:bad", v)
    qtbot.waitUntil(lambda: any(c == ("set_volume", "uuid:bad", 19) for c in anlage.calls), timeout=3000)
    vol_calls = [c for c in anlage.calls if c[0] == "set_volume"]
    assert len(vol_calls) <= 2
    assert vol_calls[-1][2] == 19


def test_play_error_emits_error_signal(qtbot, setup):
    w, anlage, host, saved, found = setup
    with qtbot.waitSignal(w.online_changed, timeout=3000):
        w.start()
    with qtbot.waitSignal(w.error, timeout=3000) as blocker:
        w.submit("play_stream", "uuid:bad", Favorite("tot", "u"))
    assert "Sender nicht erreichbar" in blocker.args[0]


def test_unknown_action_is_rejected(qtbot, setup):
    w, anlage, host, saved, found = setup
    with qtbot.waitSignal(w.online_changed, timeout=3000):
        w.start()
    with qtbot.waitSignal(w.error, timeout=3000):
        w.submit("__init__")


def test_unexpected_error_goes_offline_and_worker_survives(qtbot, setup):
    w, anlage, host, saved, found = setup
    events = []
    w.online_changed.connect(events.append)
    boom = {"on": True}
    orig = anlage.refresh

    def refresh(zones=None):
        if boom["on"]:
            raise ValueError("kaputte Antwort")
        return orig(zones)

    anlage.refresh = refresh
    w.start()
    qtbot.waitUntil(lambda: False in events, timeout=3000)
    boom["on"] = False
    qtbot.waitUntil(lambda: events and events[-1] is True, timeout=3000)
    with qtbot.waitSignal(w.state_changed, timeout=3000):
        w.submit("set_volume", "uuid:bad", 5)


# --- Final-Review-Fixes ---------------------------------------------------
def make_worker(anlage, discover=lambda last: "http://h:47365", retry=0.2, saved=None):
    return Worker(default_config(), make_anlage=lambda url: anlage, discover=discover,
                  save=(saved.append if saved is not None else lambda c: None),
                  poll_interval=0.2, retry_interval=retry)


def test_failing_first_refresh_waits_and_starts_no_poll_threads(qtbot):
    host = FakeHost()
    anlage = FakeAnlage(host)
    calls = {"n": 0}

    def bad_refresh(zones=None):
        calls["n"] += 1
        raise httpx.ReadTimeout("listDevices hängt")

    anlage.refresh = bad_refresh
    w = make_worker(anlage, retry=0.2)
    w.start()
    qtbot.wait(700)
    w.stop()
    assert calls["n"] <= 5
    assert sum(1 for t in w._threads if t.name == "raumfeld-poll") == 0


def test_long_poll_read_timeout_is_not_offline(qtbot):
    host = FakeHost()
    state = {"first": True}
    orig = host.get_zones

    def get_zones(update_id=None):
        if update_id is not None and state["first"]:
            state["first"] = False
            raise httpx.ReadTimeout("keine Änderung")
        return orig(update_id)

    host.get_zones = get_zones
    w = make_worker(FakeAnlage(host))
    events = []
    w.online_changed.connect(events.append)
    w.start()
    qtbot.waitUntil(lambda: not state["first"], timeout=3000)
    qtbot.wait(300)
    w.stop()
    assert events == [True]


def test_http_status_error_in_action_is_reported_not_offline(qtbot):
    host = FakeHost()
    anlage = FakeAnlage(host)
    req = httpx.Request("GET", "http://h/connectRoomsToZone")

    def couple(udns):
        raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))

    anlage.couple = couple
    w = make_worker(anlage)
    events = []
    w.online_changed.connect(events.append)
    with qtbot.waitSignal(w.online_changed, timeout=3000):
        w.start()
    with qtbot.waitSignal(w.error, timeout=3000) as blocker:
        w.submit("couple", ["uuid:a", "uuid:b"])
    qtbot.wait(200)
    w.stop()
    assert "404" in blocker.args[0] or "Anlage" in blocker.args[0]
    assert events == [True]


def test_action_while_offline_reports_error(qtbot):
    w = make_worker(FakeAnlage(FakeHost()), discover=lambda last: None, retry=0.2)
    w.start()
    qtbot.wait(100)
    with qtbot.waitSignal(w.error, timeout=3000) as blocker:
        w.submit("toggle_play", "uuid:bad")
    w.stop()
    assert blocker.args[0] == "Anlage nicht erreichbar"


def test_host_save_happens_in_main_thread(qtbot):
    import threading as th
    where = []
    w = Worker(default_config(), make_anlage=lambda url: FakeAnlage(FakeHost()),
               discover=lambda last: "http://h:47365",
               save=lambda c: where.append(th.current_thread() is th.main_thread()),
               poll_interval=0.2, retry_interval=0.2)
    w.start()
    qtbot.waitUntil(lambda: bool(where), timeout=3000)
    w.stop()
    assert where == [True]


def test_new_music_actions_are_allowed(qtbot):
    host = FakeHost()
    anlage = FakeAnlage(host)
    for name in ("play_container", "next_track", "previous_track", "set_play_mode"):
        setattr(anlage, name, lambda *a, _n=name: anlage._rec(_n, *a))
    w = make_worker(anlage)
    with qtbot.waitSignal(w.online_changed, timeout=3000):
        w.start()
    with qtbot.waitSignal(w.state_changed, timeout=3000):
        w.submit("play_container", "uuid:bad", "dlna-playcontainer://x?cid=0%2Fa&fii=0", "<DIDL/>")
    w.submit("next_track", "uuid:bad")
    w.submit("set_play_mode", "uuid:bad", True, "all")
    qtbot.waitUntil(lambda: any(c[0] == "set_play_mode" for c in anlage.calls), timeout=3000)
    w.stop()
    names = [c[0] for c in anlage.calls]
    assert "play_container" in names and "next_track" in names


def test_play_container_log_has_no_didl(qtbot, tmp_path):
    from deskremote.log import setup_logging
    log = setup_logging(tmp_path / "w.log")
    host = FakeHost()
    anlage = FakeAnlage(host)
    anlage.play_container = lambda *a: anlage._rec("play_container", *a)
    w = make_worker(anlage)
    with qtbot.waitSignal(w.online_changed, timeout=3000):
        w.start()
    with qtbot.waitSignal(w.state_changed, timeout=3000):
        w.submit("play_container", "uuid:bad", "dlna-playcontainer://x?sid=y&cid=0%2FMy%20Music%2FAlbums%2FX&md=0&fii=2",
                 "<DIDL-Lite>GEHEIM</DIDL-Lite>")
    w.stop()
    import logging
    for h in logging.getLogger("deskremote").handlers:
        h.flush()
    text = log.read_text(encoding="utf-8")
    assert "play_container" in text and "0/My Music/Albums/X" in text and "GEHEIM" not in text



def test_play_favorite_is_allowed_and_logged_without_didl():
    from deskremote.core.config import Favorite
    from deskremote.ui.worker import ALLOWED_ACTIONS, _log_args
    assert "play_favorite" in ALLOWED_ACTIONS
    fav = Favorite("Album 22 · Die Sterne", "dlna-playcontainer://x?cid=c&fii=0", kind="album", meta="<DIDL-Lite>geheim")
    text = _log_args("play_favorite", ("uuid:bad", fav))
    assert "Album 22" in text and "album" in text and "DIDL" not in text
