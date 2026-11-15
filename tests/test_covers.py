import json

import httpx
import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage

from deskremote.ui.covers import CoverLoader, allowed

LOCAL = "http://192.168.0.176:47366/?album=1"


@pytest.fixture(autouse=True)
def offline_dns(monkeypatch):
    """Tests nie vom echten DNS abhängig machen: Testnamen lösen auf eine öffentliche Adresse auf."""
    import socket
    from deskremote.ui import covers as cov

    def fake(host, port, *a, **k):
        if host.endswith((".example", ".orf.at")):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        raise socket.gaierror("offline")

    monkeypatch.setattr(cov.socket, "getaddrinfo", fake)
HOST = "192.168.0.176"


def jpeg(color="red", size=8) -> bytes:
    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(QColor(color))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPEG")
    return bytes(ba)


class Net:
    """Simuliertes Netz: zeichnet Anfragen auf, antwortet je nach Host."""

    def __init__(self, local=None, mb_hit=True):
        self.requests = []
        self.local = local
        self.mb_hit = mb_hit

    def __call__(self, req):
        self.requests.append(str(req.url))
        host = req.url.host
        if host == HOST:
            if self.local is None:
                return httpx.Response(404)
            return httpx.Response(200, content=self.local, headers={"content-type": "image/jpeg"})
        if host == "musicbrainz.org":
            groups = [{"id": "mbid-1"}] if self.mb_hit else []
            return httpx.Response(200, text=json.dumps({"release-groups": groups}))
        if host == "coverartarchive.org":
            return httpx.Response(200, content=jpeg("blue"), headers={"content-type": "image/jpeg"})
        raise AssertionError(f"unerwarteter Host {host}")

    def count(self, host):
        return sum(1 for u in self.requests if f"//{host}" in u)


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def loader(qtbot, tmp_path, net, online=True, clock=None):
    clock = clock or Clock()
    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(net)), tmp_path / "covers",
                     online=lambda: online, clock=clock.now, sleep=clock.sleep, run_async=False)
    return cl, clock


def test_cover_cached_after_load(qtbot, tmp_path):
    net = Net(local=jpeg())
    cl, _ = loader(qtbot, tmp_path, net)
    with qtbot.waitSignal(cl.loaded, timeout=2000):
        assert cl.request("a1", LOCAL, server_host=HOST) is None
    assert cl.request("a1", LOCAL, server_host=HOST) is not None
    assert net.count(HOST) == 1


def test_foreign_cover_url_refused(qtbot, tmp_path):
    assert allowed("http://8.8.8.8/x.jpg", HOST) is False
    assert allowed("http://evil.example/x.jpg", HOST) is False
    assert allowed("http://192.168.0.29:5000/x.jpg", HOST) is True
    assert allowed(LOCAL, HOST) is True
    net = Net(local=jpeg())
    cl, _ = loader(qtbot, tmp_path, net, online=False)
    cl.request("a1", "http://8.8.8.8/x.jpg", server_host=HOST)
    assert net.requests == []


def test_oversized_cover_ignored(qtbot, tmp_path):
    net = Net(local=b"\xff" * (2_000_001))
    cl, _ = loader(qtbot, tmp_path, net, online=False)
    cl.request("a1", LOCAL, server_host=HOST)
    assert cl.request("a1", LOCAL, server_host=HOST) is None


def test_online_lookup_only_when_local_missing(qtbot, tmp_path):
    net = Net(local=jpeg())
    cl, _ = loader(qtbot, tmp_path, net)
    cl.request("a1", LOCAL, artist="Sophie Hunger", album="Dummy Love", server_host=HOST)
    assert net.count("musicbrainz.org") == 0
    net2 = Net(local=None)
    cl2, _ = loader(qtbot, tmp_path / "b", net2)
    with qtbot.waitSignal(cl2.loaded, timeout=2000):
        cl2.request("a2", LOCAL, artist="Sophie Hunger", album="Dummy Love", server_host=HOST)
    assert net2.count("musicbrainz.org") == 1 and net2.count("coverartarchive.org") == 1
    mb = next(u for u in net2.requests if "musicbrainz" in u)
    assert "Sophie Hunger" in httpx.URL(mb).params["query"] and "Dummy Love" in httpx.URL(mb).params["query"]


def test_online_lookup_respects_setting(qtbot, tmp_path):
    net = Net(local=None)
    cl, _ = loader(qtbot, tmp_path, net, online=False)
    cl.request("a1", LOCAL, artist="A", album="B", server_host=HOST)
    assert net.count("musicbrainz.org") == 0


def test_online_result_and_miss_cached_on_disk(qtbot, tmp_path):
    net = Net(local=None)
    cl, _ = loader(qtbot, tmp_path, net)
    cl.request("a1", None, artist="A", album="B")
    miss = Net(local=None, mb_hit=False)
    cl2, _ = loader(qtbot, tmp_path, miss)
    cl2.request("a2", None, artist="X", album="Y")

    def offline(req):
        raise httpx.ConnectError("offline")

    cl3 = CoverLoader(httpx.Client(transport=httpx.MockTransport(offline)), tmp_path / "covers",
                      online=lambda: True, run_async=False)
    with qtbot.waitSignal(cl3.loaded, timeout=2000):
        cl3.request("b1", None, artist="A", album="B")
    cl3.request("b2", None, artist="X", album="Y")  # bekanntes "nichts gefunden" – kein Netz nötig
    assert cl3.request("b2", None, artist="X", album="Y") is None


def test_musicbrainz_rate_limited(qtbot, tmp_path):
    net = Net(local=None)
    cl, clock = loader(qtbot, tmp_path, net)
    cl.request("a1", None, artist="A", album="B")
    cl.request("a2", None, artist="C", album="D")
    assert net.count("musicbrainz.org") == 2
    assert sum(clock.slept) >= 1.0


def test_placeholder_hash_detected(qtbot, tmp_path):
    same = jpeg("gray")
    net = Net(local=same)
    cl, _ = loader(qtbot, tmp_path, net)
    for i in range(4):
        cl.request(f"k{i}", f"{LOCAL}&n={i}", artist=f"A{i}", album=f"B{i}", server_host=HOST)
    assert net.count("musicbrainz.org") >= 1  # spätestens ab dem dritten gleichen Bild gilt es als Platzhalter


def test_online_cover_from_foreign_host_refused(qtbot, tmp_path):
    def h(req):
        if req.url.host == "musicbrainz.org":
            return httpx.Response(200, text=json.dumps({"release-groups": [{"id": "x"}]}))
        if req.url.host == "coverartarchive.org":
            return httpx.Response(307, headers={"Location": "http://evil.example/c.jpg"})
        raise AssertionError(req.url.host)

    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(h)), tmp_path / "c", online=lambda: True,
                     run_async=False, sleep=lambda s: None)
    cl.request("a", None, artist="A", album="B")
    assert cl.request("a", None, artist="A", album="B") is None


def test_real_cover_art_archive_redirect_chain(qtbot, tmp_path):
    """Echte Kette (14.11.): coverartarchive.org → archive.org → dn….ca.archive.org"""
    def h(req):
        host = req.url.host
        if host == "musicbrainz.org":
            return httpx.Response(200, text=json.dumps({"release-groups": [{"id": "25a2e906-b7c4"}]}))
        if host == "coverartarchive.org":
            return httpx.Response(307, headers={"Location": "https://archive.org/download/mbid-x/x-250.jpg"})
        if host == "archive.org":
            return httpx.Response(302, headers={"Location": "https://dn721805.ca.archive.org/0/items/mbid-x/x-250.jpg"})
        if host == "dn721805.ca.archive.org":
            return httpx.Response(200, content=jpeg("green"))
        raise AssertionError(host)

    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(h)), tmp_path / "c", online=lambda: True,
                     run_async=False, sleep=lambda s: None)
    with qtbot.waitSignal(cl.loaded, timeout=2000):
        cl.request("a", None, artist="Sophie Hunger", album="Dummy Love")


@pytest.mark.parametrize("failure", ["503", "timeout", "caa500"])
def test_transient_failures_are_not_cached_as_missing(qtbot, tmp_path, failure):
    def h(req):
        host = req.url.host
        if host == "musicbrainz.org":
            if failure == "503":
                return httpx.Response(503)
            if failure == "timeout":
                raise httpx.ReadTimeout("langsam")
            return httpx.Response(200, text=json.dumps({"release-groups": [{"id": "abc"}]}))
        if host == "coverartarchive.org":
            return httpx.Response(500)
        raise AssertionError(host)

    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(h)), tmp_path / "c", online=lambda: True,
                     run_async=False, sleep=lambda s: None)
    cl.request("a", None, artist="A", album="B")
    assert not any(p.suffix == ".none" for p in (tmp_path / "c").glob("*")) if (tmp_path / "c").exists() else True


def test_definitive_miss_is_cached(qtbot, tmp_path):
    net = Net(local=None, mb_hit=False)
    cl, _ = loader(qtbot, tmp_path, net)
    cl.request("a", None, artist="A", album="B")
    assert any(p.suffix == ".none" for p in (tmp_path / "covers").glob("*"))


def test_logo_loaded_from_internet_and_cached(qtbot, tmp_path):
    net = {"n": 0}

    def h(req):
        net["n"] += 1
        assert req.url.host == "fm4.orf.at"
        return httpx.Response(200, content=jpeg("orange"))

    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(h)), tmp_path / "c", online=lambda: True,
                     run_async=False)
    with qtbot.waitSignal(cl.loaded, timeout=2000):
        cl.request_logo("logo:fm4", "https://fm4.orf.at/logo.png")
    cl2 = CoverLoader(httpx.Client(transport=httpx.MockTransport(h)), tmp_path / "c", online=lambda: True,
                      run_async=False)
    with qtbot.waitSignal(cl2.loaded, timeout=2000):
        cl2.request_logo("logo:fm4", "https://fm4.orf.at/logo.png")
    assert net["n"] == 1  # zweites Mal von der Platte


@pytest.mark.parametrize("target", ["http://192.168.0.1/admin.png", "http://127.0.0.1/x.png",
                                    "http://10.0.0.5/x.png", "file:///c:/x.png"])
def test_logo_never_reaches_home_network(qtbot, tmp_path, target):
    hosts = []

    def h(req):
        hosts.append(req.url.host)
        if req.url.host == "logo.example":
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, content=jpeg())

    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(h)), tmp_path / "c", online=lambda: True,
                     run_async=False)
    cl.request_logo("logo:x", "https://logo.example/l.png")
    cl.request_logo("logo:y", target)
    assert hosts == ["logo.example"]


def test_public_url_resolves_hostnames(monkeypatch):
    import socket
    from deskremote.ui import covers as cov

    table = {"evil.example": ["192.168.0.1"], "good.example": ["93.184.216.34"],
             "mixed.example": ["93.184.216.34", "10.0.0.1"]}

    def fake_getaddrinfo(host, port, *a, **k):
        if host not in table:
            raise socket.gaierror("unbekannt")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]

    monkeypatch.setattr(cov.socket, "getaddrinfo", fake_getaddrinfo)
    assert cov.public_url("https://good.example/l.png") is True
    assert cov.public_url("https://evil.example/l.png") is False
    assert cov.public_url("https://mixed.example/l.png") is False
    assert cov.public_url("https://unbekannt.example/l.png") is False
    assert cov.public_url("http://nas/l.png") is False          # einteiliger Name = LAN
    assert cov.public_url("https://good.example./l.png") is True  # abschließender Punkt


def test_strict_mode_only_server_host(qtbot, tmp_path):
    assert allowed("http://192.168.0.50/x.jpg", "192.168.0.29", strict=True) is False
    assert allowed("http://192.168.0.29:5000/x.jpg", "192.168.0.29", strict=True) is True
    assert allowed("http://192.168.0.50/x.jpg", "192.168.0.29") is True


def test_request_logo_never_resolves_in_ui_thread(qtbot, tmp_path, monkeypatch):
    import socket
    import threading
    from deskremote.ui import covers as cov
    threads = []

    def spy(host, port, *a, **k):
        threads.append(threading.current_thread() is threading.main_thread())
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(cov.socket, "getaddrinfo", spy)
    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=jpeg()))),
                     tmp_path / "c", online=lambda: True, run_async=True)
    with qtbot.waitSignal(cl.loaded, timeout=3000):
        cl.request_logo("logo:x", "https://logo.example/l.png")
    assert threads and not any(threads)  # nur im Arbeits-Thread aufgelöst


def test_disk_cover_shown_even_when_online_off(qtbot, tmp_path):
    net = Net(local=None)
    cl, _ = loader(qtbot, tmp_path, net)
    cl.request("a1", None, artist="A", album="B")  # online gefunden, auf Platte
    cl2 = CoverLoader(httpx.Client(transport=httpx.MockTransport(net)), tmp_path / "covers", online=lambda: False,
                      run_async=False)
    with qtbot.waitSignal(cl2.loaded, timeout=2000):
        cl2.request("a2", None, artist="A", album="B")


@pytest.mark.parametrize("code", [403, 429])
def test_logo_403_429_not_cached_forever(qtbot, tmp_path, code):
    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(code))), tmp_path / "c",
                     online=lambda: True, run_async=False)
    cl.request_logo("logo:x", "https://logo.example/l.png")
    assert not list((tmp_path / "c").glob("*.none")) if (tmp_path / "c").exists() else True


def test_same_url_repeated_is_no_placeholder(qtbot, tmp_path):
    net = Net(local=jpeg("gray"))
    cl, _ = loader(qtbot, tmp_path, net)
    for i in range(4):
        cl.request(f"disc{i}", LOCAL, artist="A", album=f"B{i}", server_host=HOST)  # gleiche Cover-Adresse
    assert net.count("musicbrainz.org") == 0


def test_local_art_may_redirect_to_public_https(qtbot, tmp_path):
    """Echt beobachtet: One M leitet ?artist=… per 307 auf last.fm (lastfm-img.freetls.fastly.net) weiter."""
    def h(req):
        if req.url.host == HOST:
            return httpx.Response(307, headers={"Location": "https://img.example/a.jpg"})
        if req.url.host == "img.example":
            return httpx.Response(200, content=jpeg("purple"))
        raise AssertionError(req.url.host)

    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(h)), tmp_path / "c", online=lambda: False,
                     run_async=False)
    with qtbot.waitSignal(cl.loaded, timeout=2000):
        cl.request("artist:A", "http://192.168.0.176:47366/?artist=A", server_host=HOST)


def test_local_art_redirect_into_lan_refused(qtbot, tmp_path):
    hosts = []

    def h(req):
        hosts.append(req.url.host)
        return httpx.Response(307, headers={"Location": "http://192.168.0.1/router.png"})

    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(h)), tmp_path / "c", online=lambda: False,
                     run_async=False)
    cl.request("artist:A", "http://192.168.0.176:47366/?artist=A", server_host=HOST)
    assert hosts == [HOST]


def test_drop_queued_forgets_stale_cover_jobs_but_keeps_logos(qtbot, tmp_path):
    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=jpeg()))),
                     tmp_path / "c", online=lambda: False, run_async=True)
    cl._ensure_thread = lambda: None  # Arbeits-Thread nicht starten: Warteschlange bleibt stehen
    cl.request("old:1", "http://192.168.0.176:47366/?artist=A", server_host="192.168.0.176")
    cl.request("old:2", "http://192.168.0.176:47366/?artist=B", server_host="192.168.0.176")
    cl.request_logo("logo:x", "https://logo.example/l.png")
    cl.drop_queued()
    keys = [job[0] for job in list(cl._queue.queue)]
    assert keys == ["logo:x"]
    assert "old:1" not in cl._pending and "old:1" not in cl._failed  # später erneut anforderbar



def test_drop_queued_keeps_popup_favorite_covers(qtbot, tmp_path):
    """Fix 7: Cover der Favoriten-Knöpfe nicht mit den Musikfenster-Aufträgen verwerfen."""
    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=jpeg()))),
                     tmp_path / "c", online=lambda: False, run_async=True)
    cl._ensure_thread = lambda: None
    cl.request("0/My Music/Albums/A", "http://192.168.0.176:47366/?a", server_host="192.168.0.176")
    cl.request("fav:dlna-playcontainer://x", "http://192.168.0.176:47366/?b", size=16)
    cl.drop_queued()
    assert [job[0] for job in list(cl._queue.queue)] == ["fav:dlna-playcontainer://x"]



def _svg_with_image(tmp_path) -> bytes:
    """SVG, das eine fremde Datei nachlädt – so öffnete Qt UNC-Pfade (SMB/NTLM, Audit run-2 F1)."""
    red = tmp_path / "red.png"
    img = QImage(8, 8, QImage.Format_RGB32)
    img.fill(QColor("#ff0000"))
    img.save(str(red))
    ref = str(red).replace("\\", "/")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/Album 03/xlink" '
            f'width="64" height="64"><image width="64" height="64" xlink:href="{ref}"/></svg>').encode()


def test_decode_image_rejects_svg(qtbot, tmp_path):
    from deskremote.ui.covers import decode_image
    assert decode_image(_svg_with_image(tmp_path)) is None


def test_decode_image_accepts_raster(qtbot):
    from deskremote.ui.covers import decode_image
    img = decode_image(jpeg("red"))
    assert img is not None and not img.isNull()


def test_svg_logo_is_not_loaded_nor_cached(qtbot, tmp_path):
    svg = _svg_with_image(tmp_path)
    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=svg))),
                     tmp_path / "c", online=lambda: False, run_async=False)
    assert cl._logo("https://93.184.216.34/favicon.svg") is None
    assert not list((tmp_path / "c").glob("*.img"))


def test_cached_svg_logo_is_not_decoded(qtbot, tmp_path):
    """Schon im Cache liegende SVG-Logos (ältere Version) werden beim Start nicht mehr dekodiert."""
    import hashlib
    url = "https://93.184.216.34/favicon.svg"
    cache = tmp_path / "c"
    cache.mkdir()
    (cache / ("logo-" + hashlib.sha1(url.encode()).hexdigest() + ".img")).write_bytes(_svg_with_image(tmp_path))
    cl = CoverLoader(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
                     cache, online=lambda: False, run_async=False)
    assert cl._logo(url) is None


@pytest.mark.parametrize("url", ["http://[::1]@10.0.0.5/a.jpg", "http://[::1/a.jpg"])
def test_malformed_image_urls_are_rejected_without_crash(url):
    from deskremote.ui.covers import public_url
    assert allowed(url, "10.0.0.5", strict=True) is False
    assert allowed(url, None) is False
    assert public_url(url) is False


@pytest.mark.parametrize("host", ["0.0.0.0", "169.254.1.1", "[fe80::1]", "[::]", "[::ffff:127.0.0.1]",
                                  "[::ffff:169.254.1.1]", "[2002:7f00:1::]", "[2002:a9fe:101::]",
                                  "[2001:0:4136:e378:8000:63bf:80ff:fffe]", "[2001:db8::1]", "100.64.0.1"])
def test_lax_allowed_rejects_unspecified_and_link_local(host):
    assert allowed(f"http://{host}/a.jpg", None) is False



@pytest.mark.parametrize("host", ["192.168.0.176", "10.0.0.5", "172.16.3.4", "[fd00::1]", "[::ffff:192.168.0.5]"])
def test_lax_allowed_accepts_home_network(host):
    assert allowed(f"http://{host}:47366/a.jpg", None) is True
