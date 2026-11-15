"""Sendersuche über radio-browser.info."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from ..i18n import tr
from .config import Favorite
from .text import clean_text

USER_AGENT = "DeskRemote/0.1"
DEFAULT_SERVERS = ["de1.api.radio-browser.info", "de2.api.radio-browser.info", "fi1.api.radio-browser.info"]
TIMEOUT = 3.0
DEADLINE = 3.5  # inkl. Namensauflösung, die httpx-Timeouts nicht abdecken


def _web_url(value) -> str | None:
    text = str(value or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else None


def is_hls(url: str) -> bool:
    """HLS-Wiedergabeliste (.m3u8): spielt Raumfeld nicht – ein Versuch ließ den Zonen-Renderer neu starten."""
    try:
        return urlsplit(url).path.lower().endswith(".m3u8")
    except ValueError:
        return False


def _num(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass
class Station:
    uuid: str
    name: str
    url: str
    codec: str
    bitrate: int
    votes: int
    country: str
    favicon: str | None = None
    hls: bool = False  # spielt auf Raumfeld nicht


class SearchError(Exception):
    pass


class RadioBrowser:
    def __init__(self, client: httpx.Client, servers: list[str] | None = None, deadline: float = DEADLINE):
        self.client = client
        self.servers = list(servers or DEFAULT_SERVERS)
        self.deadline = deadline

    def _fetch(self, server: str, params: dict) -> object:
        resp = self.client.get(
            f"https://{server}/json/stations/search", params=params,
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def search(self, query: str, country: str | None = "AT", limit: int = 30) -> list[Station]:
        params: dict[str, str | int] = {
            "name": query, "limit": limit, "order": "votes", "reverse": "true", "hidebroken": "true",
        }
        if country:
            params["countrycode"] = country
        for server in self.servers:
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                data = pool.submit(self._fetch, server, params).result(timeout=self.deadline)
            except (FutureTimeout, httpx.HTTPError, ValueError):
                continue
            finally:
                pool.shutdown(wait=False)
            if not isinstance(data, list):
                continue
            return [
                Station(
                    uuid=str(d.get("stationuuid", "")),
                    name=clean_text(d.get("name", ""), 120),
                    url=str(d.get("url_resolved") or ""),
                    codec=clean_text(d.get("codec", ""), 12),
                    bitrate=_num(d.get("bitrate")),
                    votes=_num(d.get("votes")),
                    country=str(d.get("countrycode", "")),
                    favicon=_web_url(d.get("favicon")),
                    hls=d.get("hls") in (1, "1", True) or is_hls(str(d.get("url_resolved") or "")),
                )
                for d in data
                if isinstance(d, dict) and str(d.get("url_resolved") or "").startswith(("http://", "https://"))
            ]
        raise SearchError(tr("Senderverzeichnis nicht erreichbar"))

    @staticmethod
    def to_favorite(st: Station) -> Favorite:
        return Favorite(st.name, st.url, st.favicon or "")

    def logo_for(self, stream_url: str) -> str | None:
        """Logo eines bekannten Senders über seine Stream-Adresse nachschlagen (für alte Favoriten)."""
        for server in self.servers:
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                def fetch():
                    resp = self.client.get(f"https://{server}/json/stations/byurl", params={"url": stream_url},
                                           headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
                    resp.raise_for_status()
                    return resp.json()
                data = pool.submit(fetch).result(timeout=self.deadline)
            except (FutureTimeout, httpx.HTTPError, ValueError):
                continue
            finally:
                pool.shutdown(wait=False)
            if isinstance(data, list):
                return next((u for d in data if isinstance(d, dict) and (u := _web_url(d.get("favicon")))), None)
        return None
