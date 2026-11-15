"""Cover und Logos laden: erst vom Heimnetz, fehlt dort etwas, bei MusicBrainz / Cover Art Archive.

- Netzwerk nie im UI-Thread (eigener Arbeits-Thread, ein Auftrag nach dem anderen).
- Bilder aus dem Heimnetz nur von der Medienserver-Adresse oder privaten IPs.
- Online: höchstens 1 Anfrage/s an MusicBrainz, Treffer und Fehlversuche dauerhaft auf der Platte.
"""

from __future__ import annotations

import hashlib
import ipaddress
import queue
import socket
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

from .. import __version__

MAX_BYTES = 2_000_000
MEMORY_ITEMS = 300
PLACEHOLDER_REPEATS = 3
TIMEOUT_LOCAL = 3.0
TIMEOUT_ONLINE = 5.0
MB_INTERVAL = 1.0
USER_AGENT = f"DeskRemote/{__version__} ( private use )"
_ONLINE_HOSTS = ("musicbrainz.org", "coverartarchive.org")


# Nur Rasterformate: SVG lädt per <image href> fremde Dateien nach – auch \\host\… über SMB,
# dann sendet Windows den NTLM-Hash an den Angreifer (Sicherheits-Audit run-2, F1).
RASTER_FORMATS = {b"jpeg", b"jpg", b"png", b"gif", b"ico", b"webp", b"bmp"}


HOME_NETWORKS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]


def decode_image(data: bytes) -> QImage | None:
    """Bild aus fremden Bytes – nur Rasterformate, nie SVG oder andere Formate mit Nachladen."""
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.ReadOnly)
    reader = QImageReader(buf)
    if bytes(reader.format()).lower() not in RASTER_FORMATS:
        return None
    image = reader.read()
    return None if image.isNull() else image


def _split(url: str):
    """urlsplit, das bei kaputten Adressen (z. B. "http://[::1") None liefert statt zu werfen."""
    try:
        parts = urlsplit(url)
        return parts if parts.hostname else None
    except ValueError:
        return None


def allowed(url: str | None, server_host: str | None, strict: bool = False) -> bool:
    """Bilder aus dem Heimnetz: vom Medienserver selbst oder (nicht strikt) einer privaten, nicht-loopback IP.

    strict=True für fremde DLNA-Server: nur deren eigene Adresse.
    """
    if not url:
        return False
    parts = _split(url)
    if parts is None or parts.scheme not in ("http", "https"):
        return False
    if server_host and parts.hostname == server_host:
        return True
    if strict:
        return False
    try:
        ip = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # nur echte Heimnetz-Bereiche – keine IPv6-Sonderformen (6to4, Teredo, Doku-Netze) mit eingebetteten Zielen
    return any(ip in net for net in HOME_NETWORKS)


def public_url(url: str | None) -> bool:
    """Internet-Adresse für Senderlogos: http(s), kein Heimnetz-/Loopback-/Link-Local-Ziel als IP."""
    if not url:
        return False
    parts = _split(url)
    if parts is None or parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".local") or "." not in host:
        return False  # einteilige Namen und .local zeigen ins Heimnetz
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    # Hostname: alle aufgelösten Adressen müssen öffentlich sein (Restrisiko DNS-Rebinding bleibt)
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


def _online_host_ok(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host in _ONLINE_HOSTS or host == "archive.org" or host.endswith(".archive.org"))


def _lucene(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class CoverLoader(QObject):
    loaded = Signal(str, QPixmap)
    _done = Signal(str, QImage)

    def __init__(self, client: httpx.Client, cache_dir: Path, online: Callable[[], bool],
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 run_async: bool = True):
        super().__init__()
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.online = online
        self.clock = clock
        self.sleep = sleep
        self.run_async = run_async
        self._memory: OrderedDict[str, QPixmap] = OrderedDict()
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        self._hash_keys: dict[str, set[str]] = {}
        self._placeholders: set[str] = set()
        self._last_mb = -1e9
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._done.connect(self._on_done)

    # --- öffentlich -----------------------------------------------------
    def request(self, key: str, url: str | None, artist: str | None = None, album: str | None = None,
                size: int = 48, server_host: str | None = None, strict: bool = False) -> QPixmap | None:
        if key in self._memory:
            self._memory.move_to_end(key)
            return self._memory[key]
        if key in self._pending or key in self._failed:
            return None
        self._pending.add(key)
        if strict and not allowed(url, server_host, strict=True):
            url = None  # fremder Server: Cover nur von seiner eigenen Adresse
        job = (key, url, artist, album, size, server_host)
        if self.run_async:
            self._queue.put(job)
            self._ensure_thread()
        else:
            self._work(job)
        return None

    def request_logo(self, key: str, url: str | None, size: int = 16) -> QPixmap | None:
        """Senderlogo aus dem Internet (radio-browser-Adresse), mit Plattencache."""
        if key in self._memory:
            return self._memory[key]
        if key in self._pending or key in self._failed:
            return None
        if not url or urlsplit(url).scheme not in ("http", "https"):
            return None  # nur Syntax hier – Namensauflösung/Netzprüfung im Arbeits-Thread (_fetch_public)
        self._pending.add(key)
        job = (key, url, None, None, size, "__logo__")
        if self.run_async:
            self._queue.put(job)
            self._ensure_thread()
        else:
            self._work(job)
        return None

    def drop_queued(self) -> None:
        """Noch nicht begonnene Cover-Aufträge verwerfen (Ansicht gewechselt); Senderlogos bleiben."""
        keep = []
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            if job[5] == "__logo__" or job[0].startswith("fav:"):  # Senderlogos und Favoriten-Cover bleiben
                keep.append(job)
            else:
                self._pending.discard(job[0])
        for job in keep:
            self._queue.put(job)

    # --- intern ---------------------------------------------------------
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="cover-loader", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=30)
            except queue.Empty:
                return
            self._work(job)

    def _work(self, job) -> None:
        key, url, artist, album, size, server_host = job
        image = None
        try:
            if server_host == "__logo__":
                image = self._logo(url)
                server_host = None
            else:
                image = self._local(key, url, server_host)
            if image is None and artist and album:
                image = self._from_disk_or_online(artist, album, allow_network=self.online())
        except Exception:  # noqa: BLE001 – ein fehlendes Cover darf nie etwas anderes stören
            image = None
        if image is None:
            self._pending.discard(key)
            self._failed.add(key)
            return
        self._done.emit(key, image.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _on_done(self, key: str, image: QImage) -> None:
        pix = QPixmap.fromImage(image)
        self._pending.discard(key)
        self._memory[key] = pix
        while len(self._memory) > MEMORY_ITEMS:
            self._memory.popitem(last=False)
        self.loaded.emit(key, pix)

    @staticmethod
    def _read_capped(resp: httpx.Response) -> bytes | None:
        """Body lesen, aber abbrechen, sobald er größer als MAX_BYTES wird."""
        if resp.status_code != 200:
            return None
        data = bytearray()
        for chunk in resp.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                return None
        return bytes(data)

    def _fetch(self, url: str, timeout: float) -> bytes | None:
        """Heimnetz: Weiterleitung nur zu einer öffentlichen HTTPS-Adresse (One M → last.fm), nie ins LAN."""
        with self.client.stream("GET", url, timeout=timeout, follow_redirects=False,
                                headers={"User-Agent": USER_AGENT}) as resp:
            if not resp.is_redirect:
                return self._read_capped(resp)
            target = str(resp.url.join(resp.headers.get("Location", "")))
        if urlsplit(target).scheme != "https":
            return None
        status, data = self._fetch_public(target, 1)
        return data if status == 200 else None

    def _fetch_follow(self, url: str, timeout: float, hops: int) -> tuple[int, bytes | None]:
        """Online: Weiterleitungen nur zu MusicBrainz/Cover Art Archive/archive.org, max. 3.

        Liefert (HTTP-Status, Daten); Status 0 = abgelehnt (fremder Host, zu groß, zu viele Sprünge).
        """
        with self.client.stream("GET", url, timeout=timeout, follow_redirects=False,
                                headers={"User-Agent": USER_AGENT}) as resp:
            if not resp.is_redirect:
                data = self._read_capped(resp)
                return (resp.status_code if data is not None or resp.status_code != 200 else 0), data
            target = str(resp.url.join(resp.headers.get("Location", "")))
        if hops >= 3 or not _online_host_ok(target):
            return 0, None
        return self._fetch_follow(target, timeout, hops + 1)

    def _logo(self, url: str) -> QImage | None:
        path = self.cache_dir / ("logo-" + hashlib.sha1(url.encode("utf-8")).hexdigest())
        img_file, none = path.with_suffix(".img"), path.with_suffix(".none")
        if img_file.exists():
            return decode_image(img_file.read_bytes())
        if none.exists():
            return None
        status, data = self._fetch_public(url, 0)
        if status == 0 or status >= 500 or status in (403, 429):
            return None  # abgelehnt oder vorübergehend (Sperre/zu viele Anfragen)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        image = decode_image(data) if status == 200 and data else None
        if image is None:
            none.touch()
            return None
        img_file.write_bytes(data)
        return image

    def _fetch_public(self, url: str, hops: int) -> tuple[int, bytes | None]:
        """Wie _fetch_follow, aber jede Station der Kette muss öffentlich sein (kein Sprung ins Heimnetz)."""
        if not public_url(url):
            return 0, None
        try:
            with self.client.stream("GET", url, timeout=TIMEOUT_LOCAL, follow_redirects=False,
                                    headers={"User-Agent": USER_AGENT}) as resp:
                if not resp.is_redirect:
                    data = self._read_capped(resp)
                    return (resp.status_code if data is not None or resp.status_code != 200 else 0), data
                target = str(resp.url.join(resp.headers.get("Location", "")))
        except httpx.HTTPError:
            return 599, None
        if hops >= 3:
            return 0, None
        return self._fetch_public(target, hops + 1)

    def _local(self, key: str, url: str | None, server_host: str | None) -> QImage | None:  # noqa: ARG002
        if not allowed(url, server_host):
            return None
        try:
            data = self._fetch(url, TIMEOUT_LOCAL)
        except httpx.HTTPError:
            return None
        if not data:
            return None
        digest = hashlib.sha1(data).hexdigest()
        with self._lock:
            urls = self._hash_keys.setdefault(digest, set())
            urls.add(url)  # verschiedene Adressen zählen – Mehrfach-CDs mit gleicher Adresse sind kein Platzhalter
            if len(urls) >= PLACEHOLDER_REPEATS:
                self._placeholders.add(digest)  # dasselbe Bild bei vielen Alben = Raumfeld-Platzhalter
            if digest in self._placeholders:
                return None
        return decode_image(data)

    def _disk_path(self, artist: str, album: str) -> Path:
        name = hashlib.sha1(f"{artist.lower()}|{album.lower()}".encode("utf-8")).hexdigest()
        return self.cache_dir / name

    def _from_disk_or_online(self, artist: str, album: str, allow_network: bool = True) -> QImage | None:
        base = self._disk_path(artist, album)
        jpg, none = base.with_suffix(".jpg"), base.with_suffix(".none")
        if jpg.exists():
            return decode_image(jpg.read_bytes())
        if none.exists() or not allow_network:
            return None  # Platte immer, Netz nur mit Einwilligung
        outcome, data = self._musicbrainz(artist, album)
        if outcome == "error":
            return None  # vorübergehend (503, Zeitüberlauf, Serverfehler) – später erneut versuchen
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        image = decode_image(data) if outcome != "miss" and data is not None else None
        if image is None:
            none.touch()  # sicher kein Cover vorhanden – nie wieder fragen
            return None
        jpg.write_bytes(data)
        return image

    def _wait_rate_limit(self) -> None:
        wait = self._last_mb + MB_INTERVAL - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._last_mb = self.clock()

    def _musicbrainz(self, artist: str, album: str) -> tuple[str, bytes | None]:
        """("hit", Bild) · ("miss", None) = sicher nichts da · ("error", None) = später nochmal."""
        self._wait_rate_limit()
        query = f'artist:"{_lucene(artist)}" AND releasegroup:"{_lucene(album)}"'
        try:
            resp = self.client.get("https://musicbrainz.org/ws/2/release-group/",
                                   params={"query": query, "fmt": "json", "limit": 1},
                                   headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_ONLINE,
                                   follow_redirects=False)
            if resp.status_code != 200:
                return "error", None
            groups = resp.json().get("release-groups") or []
            if not groups or not isinstance(groups[0], dict) or not groups[0].get("id"):
                return "miss", None
            mbid = str(groups[0]["id"])
            if not all(c.isalnum() or c == "-" for c in mbid):
                return "miss", None
            status, data = self._fetch_follow(
                f"https://coverartarchive.org/release-group/{mbid}/front-250", TIMEOUT_ONLINE, 0)
        except (httpx.HTTPError, ValueError):
            return "error", None
        if status == 200 and data:
            return "hit", data
        if status == 404:
            return "miss", None  # Album bekannt, aber kein Cover hinterlegt
        return "error", None
