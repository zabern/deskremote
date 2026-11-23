"""Musikfenster: Alben (Start), Künstler, Ordner und Suche – abspielen im Ziel-Raum."""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..i18n import tr
from ..core.config import MAX_META, Config, Favorite
from ..core.text import clean_text
from ..raumfeld.media import (
    ALBUMS, ARTISTS, FOLDERS, PAGE_SIZE, SEARCH_ALBUMS, SEARCH_ARTISTS, MediaContainer, Page, container_uri,
    normalize_raumfeld_album,
)
from .theme import ICON_PLAY, STYLESHEET, PlainLabel, app_icon

DEBOUNCE_MS = 400
ROOT = "0"  # Wurzel fremder DLNA-Server


@dataclass
class View:
    heading: str
    object_id: str
    search: str | None = None
    albums_only: bool = False
    container: MediaContainer | None = None  # gesetzt, wenn die Ansicht abspielbar ist (Album/Ordner)
    items: list = field(default_factory=list)
    total: int = 0
    fetched: int = 0  # roh vom Server erhaltene Einträge (Grundlage fürs Nachladen)


class MusicWindow(QWidget):
    play = Signal(str, str)  # Transport-URI, Metadaten
    play_shuffled = Signal(str, str)  # dito, im Zufallsmodus (Künstler: alle Titel)
    _loaded = Signal(int, object, bool)  # Generation, Page oder Fehlertext, anhängen?
    _meta_ready = Signal(str, str, bool)
    _servers_ready = Signal(object)
    favorites_changed = Signal()
    add_requested = Signal(str)  # Objekt-ID → in die Teufel-Favoriten (sobald die Anlage-Liste da ist)
    _fav_ready = Signal(object, str)  # Favorite oder None, objectName des Sterns

    def __init__(self, servers: list, covers, cfg: Config, save: Callable[[Config], None],
                 run_async: bool = True, target: Callable[[], str | None] | None = None):
        super().__init__(None, Qt.Window)
        self.covers = covers
        self.cfg = cfg
        self.save = save
        self.run_async = run_async
        self.target = target or (lambda: cfg.target_room)
        self._servers: list = []
        self._stack: list[View] = []
        self._generation = 0
        self._loading = False
        self._cover_labels: dict[str, PlainLabel] = {}
        self.setWindowTitle(tr("Musik"))
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLESHEET)
        self.resize(560, 640)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.source = QComboBox()
        self.source.setObjectName("source")
        self.source.currentIndexChanged.connect(self._source_changed)
        top.addWidget(PlainLabel(tr("Quelle:")))
        top.addWidget(self.source, 1)
        root.addLayout(top)

        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setObjectName("query")
        self.query.setPlaceholderText(tr("Album suchen …"))
        row.addWidget(self.query, 1)
        for text, name, slot in ((tr("Alben"), "albums", self.show_albums), (tr("Künstler"), "artists", self.show_artists),
                                 (tr("Ordner"), "folders", self.show_folders)):
            b = QPushButton(text)
            b.setObjectName(f"mode:{name}")
            b.setProperty("flat_link", True)
            b.clicked.connect(slot)
            row.addWidget(b)
        root.addLayout(row)

        nav = QHBoxLayout()
        self.back_btn = QPushButton(tr("‹ Zurück"))
        self.back_btn.setObjectName("back")
        self.back_btn.setProperty("flat_link", True)
        self.back_btn.clicked.connect(self.back)
        nav.addWidget(self.back_btn)
        self.heading = PlainLabel("")
        self.heading.setObjectName("heading")
        self.heading.setWordWrap(True)
        nav.addWidget(self.heading, 1)
        root.addLayout(nav)

        self.status = PlainLabel("")
        self.status.setObjectName("status")
        self.status.setProperty("muted", True)
        root.addWidget(self.status)

        self._list_host = QWidget()
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(2)
        self._list.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self._list_host)
        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_more)
        root.addWidget(self.scroll, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(lambda: self.search_now(self.query.text()))
        self.query.textChanged.connect(lambda _t: self._timer.start())
        self._loaded.connect(self._on_loaded)
        self._meta_ready.connect(self._on_meta)
        self._servers_ready.connect(self.set_servers)
        self._fav_ready.connect(self._on_fav_ready)
        if covers is not None:
            covers.loaded.connect(self._on_cover)

        self.set_servers(servers)

    # --- Quellen --------------------------------------------------------
    def set_servers(self, servers) -> None:
        if servers is None:
            self._servers = []
            self.source.clear()
            self._clear()
            self.status.setText(tr("Quelle nicht erreichbar"))
            return
        self._servers = list(servers)
        self.source.blockSignals(True)
        self.source.clear()
        for s in self._servers:
            self.source.addItem(tr("Meine Musik (Raumfeld)") if s.is_raumfeld else f"DLNA: {clean_text(s.name, 60)}", s.udn)
        idx = next((i for i, s in enumerate(self._servers) if s.udn == self.cfg.music_source), 0)
        if self._servers:
            self.source.setCurrentIndex(idx)
        self.source.blockSignals(False)
        if not self._servers:
            self._clear()
            self.status.setText(tr("Keine Musikquelle gefunden – Musikordner in der Teufel-App hinzufügen"))
            return
        self.show_artists()  # Start mit Künstlern

    def discover(self, find: Callable[[], list]) -> None:
        """Quellen im Hintergrund suchen; das Fenster ist sofort bedienbar."""
        self._clear()
        self.status.setText(tr("Suche Musikquellen …"))

        def work():
            try:
                servers: object = find()
            except Exception:  # noqa: BLE001 – Raumfeld-Host nicht erreichbar o. Ä.
                servers = None
            self._servers_ready.emit(servers)

        if self.run_async:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    def _server(self):
        idx = self.source.currentIndex()
        return self._servers[idx] if 0 <= idx < len(self._servers) else None

    def _source_changed(self) -> None:
        server = self._server()
        if server is None:
            return
        self.cfg.music_source = server.udn
        self.save(self.cfg)
        self.show_artists()

    # --- Ansichten ------------------------------------------------------
    def _start(self, view: View) -> None:
        self._stack = [view]
        self._load()

    def _set_search_mode(self, artists: bool) -> None:
        """Das Suchfeld sucht, was gerade angezeigt wird: Künstler in der Künstleransicht, sonst Alben."""
        if artists != getattr(self, "_search_artists", artists) and self.query.text():
            self.query.blockSignals(True)  # Leeren soll keine neue Suche auslösen
            self.query.clear()
            self.query.blockSignals(False)
            self._timer.stop()
        self._search_artists = artists
        self.query.setPlaceholderText(tr("Künstler suchen …") if artists else tr("Album suchen …"))

    def show_albums(self) -> None:
        server = self._server()
        self._set_search_mode(False)
        if server is not None:
            self._start(View(tr("Alben"), ALBUMS) if server.is_raumfeld else View(tr("Musik"), ROOT))

    def show_artists(self) -> None:
        server = self._server()
        self._set_search_mode(server is not None and server.is_raumfeld)
        if server is not None:
            self._start(View(tr("Künstler"), ARTISTS) if server.is_raumfeld else View(tr("Musik"), ROOT))

    def show_folders(self) -> None:
        server = self._server()
        self._set_search_mode(False)
        if server is not None:
            self._start(View(tr("Ordner"), FOLDERS) if server.is_raumfeld else View(tr("Musik"), ROOT))

    def open_container(self, c: MediaContainer) -> None:
        heading = f"{c.title} · {c.artist}" if c.artist else c.title
        self._stack.append(View(heading, c.id, albums_only=(c.kind == "artist"), container=c))
        self._load()

    def back(self) -> None:
        if len(self._stack) > 1:
            self._stack.pop()
            self._load()

    def search_now(self, text: str) -> None:
        text = text.strip()
        server = self._server()
        if server is None:
            return
        artists = getattr(self, "_search_artists", False) and server.is_raumfeld
        if not text:
            self.show_artists() if artists else self.show_albums()
            return
        target = (SEARCH_ARTISTS if artists else SEARCH_ALBUMS) if server.is_raumfeld else ROOT
        self._start(View(tr("Suche: {text}", text=text), target, search=text))

    def scroll_to_end(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._maybe_more()

    # --- Laden ----------------------------------------------------------
    def _load(self, append: bool = False) -> None:
        view, server = self._stack[-1], self._server()
        if server is None:
            return
        self._generation += 1
        gen = self._generation
        start = view.fetched if append else 0
        self._loading = True
        if not append:
            self._clear()
            self.status.setText(tr("Lade …"))
        self._update_nav()

        def work():
            try:
                if view.search is not None:
                    result: object = server.search(view.object_id, view.search, start=start, count=PAGE_SIZE)
                else:
                    result = server.browse(view.object_id, start=start, count=PAGE_SIZE)
            except Exception:  # noqa: BLE001 – Quelle weg oder kaputte Antwort
                result = tr("Quelle nicht erreichbar")
            self._loaded.emit(gen, result, append)

        if self.run_async:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    def _on_loaded(self, gen: int, result, append: bool) -> None:
        if gen != self._generation:
            return  # veraltet
        self._loading = False
        view = self._stack[-1]
        if isinstance(result, str):
            self.status.setText(result)
            return
        page: Page = result
        server = self._server()
        if server is not None and server.is_raumfeld:
            page = Page([normalize_raumfeld_album(i, server.host) if isinstance(i, MediaContainer) else i
                         for i in page.items], page.total, page.start)
        items = [i for i in page.items if not (view.albums_only and isinstance(i, MediaContainer) and i.kind != "album")]
        offset = len(view.items) if append else 0
        if not append:
            view.items = []
            view.fetched = 0
        view.items.extend(page.items if not view.albums_only else items)
        view.fetched += len(page.items)
        view.total = page.total
        for n, item in enumerate(items):
            self._list.insertWidget(self._list.count() - 1, self._row(offset + n, item))
        self.status.setText("" if view.items else tr("Nichts gefunden"))
        self._update_nav()
        if page.items:
            QTimer.singleShot(0, self._maybe_more)  # Fenster noch nicht voll → gleich weiterladen

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._maybe_more)

    def _maybe_more(self, *_args) -> None:
        if not self._stack or self._loading:
            return
        view = self._stack[-1]
        bar = self.scroll.verticalScrollBar()
        if view.fetched < view.total and bar.value() >= bar.maximum() - 40:
            self._load(append=True)

    # --- Darstellung ----------------------------------------------------
    def _update_nav(self) -> None:
        view = self._stack[-1] if self._stack else None
        self.heading.setText(view.heading if view else "")
        self.back_btn.setVisible(len(self._stack) > 1)

    def _clear(self) -> None:
        self._cover_labels.clear()
        if self.covers is not None:
            self.covers.drop_queued()  # Cover der alten Ansicht nicht mehr abarbeiten
        while self._list.count() > 1:
            w = self._list.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _row(self, index: int, item) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        if isinstance(item, MediaContainer):
            cover = PlainLabel("")
            cover.setObjectName(f"cover:{index}")
            cover.setFixedSize(40, 40)
            lay.addWidget(cover)
            self._attach_cover(cover, item)
            show_artist = item.artist and item.kind != "artist" and item.artist.casefold() != item.title.casefold()
            title = PlainLabel(f"{item.title} · {item.artist}" if show_artist else item.title)
            title.setObjectName(f"title:{index}")
            title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lay.addWidget(title, 1)
            if item.child_count is not None and item.kind == "album":
                count = PlainLabel(tr("{count} Titel", count=item.child_count))
                count.setProperty("muted", True)
                lay.addWidget(count)
            open_btn = QPushButton("›")
            open_btn.setObjectName(f"open:{index}")
            open_btn.setProperty("round", True)
            open_btn.setToolTip(tr("Öffnen"))
            open_btn.clicked.connect(lambda _=False, c=item: self.open_container(c))
            lay.addWidget(open_btn)
            artist_play = item.kind == "artist" and self._raumfeld_source()
            if item.kind in ("album", "folder") or artist_play:
                play = QPushButton(ICON_PLAY)
                play.setObjectName(f"play:{index}")
                play.setProperty("round", True)
                play.setProperty("icon_btn", True)
                if artist_play:
                    play.setToolTip(tr("Alle Titel im Zufallsmodus"))
                    play.clicked.connect(lambda _=False, c=item: self._play_artist(c))
                else:
                    play.setToolTip(tr("Abspielen"))
                    play.clicked.connect(lambda _=False, c=item: self._play_container(c, 0))
                lay.addWidget(play)
            if item.kind in ("album", "artist") and self._raumfeld_source():
                lay.addWidget(self._container_star(index, item))
        else:
            number = f"{item.number}  " if item.number else ""
            title = PlainLabel(f"{number}{item.title}")
            title.setObjectName(f"track:{index}")
            title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lay.addWidget(title, 1)
            if item.duration:
                dur = PlainLabel(item.duration.split(".")[0].removeprefix("0:"))
                dur.setProperty("muted", True)
                lay.addWidget(dur)
            play = QPushButton(ICON_PLAY)
            play.setObjectName(f"playtrack:{index}")
            play.setProperty("round", True)
            play.setProperty("icon_btn", True)
            play.setToolTip(tr("Ab diesem Titel abspielen"))
            play.clicked.connect(lambda _=False, i=index: self._play_view(i))
            lay.addWidget(play)
            view = self._stack[-1] if self._stack else None
            if view is not None and view.container is not None and self._raumfeld_source():
                c = view.container
                name = f"{item.title} · {item.artist}" if item.artist else item.title
                lay.addWidget(self._star(f"startrack:{index}", "track", c.id, index, name, c.art_url,
                                         object_id=item.id))
        return w

    # --- Favoriten ------------------------------------------------------
    def _fav_exists(self, kind: str, url: str) -> bool:
        return any(f.kind == kind and f.url == url for f in self.cfg.favorites)

    def _container_star(self, index: int, c: MediaContainer) -> QPushButton:
        if c.kind == "artist":
            return self._star(f"star:{index}", "artist", f"{c.id}/AllTracks", 0, tr("{name} · Zufall", name=c.title),
                              c.art_url, object_id=c.id)
        name = f"{c.title} · {c.artist}" if c.artist else c.title
        return self._star(f"star:{index}", "album", c.id, 0, name, c.art_url, object_id=c.id)

    # Teufel-Favoriten ---------------------------------------------------
    def _anlage_mode(self) -> bool:
        return self.cfg.anlage_favorites is not None

    def _on_anlage(self, object_id: str) -> bool:
        """Steht das Objekt schon in den Teufel-Favoriten? Alben auch unter ihrem zweiten Pfad erkennen
        (…/Albums/A+X und …/Artists/A/A+X enden gleich)."""
        targets = {f.target for f in self.cfg.anlage_favorites or [] if f.on_anlage}
        if object_id in targets:
            return True
        tail = object_id.rsplit("/", 1)[-1]
        return (object_id.startswith("0/My Music/") and "+" in tail
                and any(t.startswith("0/My Music/") and t.rsplit("/", 1)[-1] == tail for t in targets))

    def refresh_stars(self) -> None:
        """Nach neuer Teufel-Liste (auch aus der Teufel-App): Sterne passend füllen."""
        if not self._anlage_mode():
            return
        for star in self.findChildren(QPushButton):
            target = star.property("target")
            if target:
                star.setText("★" if self._on_anlage(target) else "☆")

    def _star(self, object_name: str, kind: str, container_id: str, start: int, name: str,
              cover: str | None, object_id: str = "") -> QPushButton:
        if self._anlage_mode() and object_id:
            b = QPushButton("★" if self._on_anlage(object_id) else "☆")
            b.setObjectName(object_name)
            b.setProperty("target", object_id)
            b.setProperty("round", True)
            b.setToolTip(tr("Zu den Teufel-Favoriten (auch in der Teufel-App)"))
            b.clicked.connect(lambda _=False: self._add_to_anlage(object_id, b))
            return b
        server = self._server()
        url = container_uri(server.udn, container_id, start) if server is not None else ""
        b = QPushButton("★" if self._fav_exists(kind, url) else "☆")
        b.setObjectName(object_name)
        b.setProperty("fav_key", f"{kind} {url}")
        b.setProperty("round", True)
        b.setToolTip(tr("Als Favorit speichern"))
        b.clicked.connect(lambda _=False: self._add_favorite(object_name, kind, container_id, start, name, cover))
        return b

    def _add_to_anlage(self, object_id: str, button: QPushButton) -> None:
        server = self._server()
        if server is None or not server.is_raumfeld or self._on_anlage(object_id):
            return
        button.setText("…")  # die Anlage bestätigt mit der neuen Liste
        self.add_requested.emit(object_id)

    def _add_favorite(self, star_name: str, kind: str, container_id: str, start: int, name: str,
                      cover: str | None) -> None:
        server = self._server()
        if server is None or not server.is_raumfeld:
            return
        url = container_uri(server.udn, container_id, start)
        if self._fav_exists(kind, url):
            return
        if cover and not cover.lower().startswith(("http://", "https://")):
            cover = None

        def work():
            try:
                meta = server.metadata(container_id)
                count = server.browse(container_id, 0, 1).total if kind == "artist" else 0
            except Exception:  # noqa: BLE001
                meta, count = "", 0
            fav = Favorite(clean_text(name, 120), url, None, kind, meta, cover, max(0, int(count))) if meta else None
            self._fav_ready.emit(fav, star_name)

        if self.run_async:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    def _on_fav_ready(self, fav, star_name: str) -> None:
        if fav is None:
            self.status.setText(tr("Quelle nicht erreichbar"))
            return
        if len(fav.meta) > MAX_META:
            self.status.setText(tr("Dieser Eintrag ist zu groß für einen Favoriten"))  # sonst wäre die Config ungültig
            return
        if self._fav_exists(fav.kind, fav.url):
            return
        self.cfg.favorites.append(fav)
        self.save(self.cfg)
        self.favorites_changed.emit()
        key = f"{fav.kind} {fav.url}"  # nach Ansichtswechsel nie den falschen Stern füllen
        for star in self.findChildren(QPushButton):
            if star.property("fav_key") == key:
                star.setText("★")
        self.status.setText(tr("Favorit gespeichert: {name}", name=fav.name))

    def _attach_cover(self, label: PlainLabel, item: MediaContainer) -> None:
        if self.covers is None:
            return
        server = self._server()
        self._cover_labels[item.id] = label
        pix = self.covers.request(item.id, item.art_url, artist=item.artist,
                                  album=item.title if item.kind == "album" else None, size=40,
                                  server_host=getattr(server, "host", None),
                                  strict=not getattr(server, "is_raumfeld", False))
        if pix is not None:
            label.setPixmap(pix)

    def _on_cover(self, key: str, pix: QPixmap) -> None:
        label = self._cover_labels.get(key)
        if label is not None:
            label.setPixmap(pix)

    # --- Abspielen ------------------------------------------------------
    def _play_view(self, start_index: int) -> None:
        view = self._stack[-1]
        if view.container is not None:
            self._play_container(view.container, start_index)

    def _on_meta(self, uri: str, meta: str, shuffle: bool) -> None:
        if not meta:
            self.status.setText(tr("Quelle nicht erreichbar"))  # nie ohne Metadaten an die Anlage senden
            return
        (self.play_shuffled if shuffle else self.play).emit(uri, meta)

    def _raumfeld_source(self) -> bool:
        server = self._server()
        return server is not None and server.is_raumfeld

    def _play_artist(self, c: MediaContainer) -> None:
        """Raumfeld führt je Künstler einen Ordner „All Tracks“ – den im Zufallsmodus, ab zufälligem Titel."""
        all_tracks = MediaContainer(f"{c.id}/AllTracks", c.title, c.artist, None, None, "folder")
        self._play_container(all_tracks, None, shuffle=True)

    def _play_container(self, c: MediaContainer, start_index: int | None, shuffle: bool = False) -> None:
        server = self._server()
        if server is None:
            return
        if not self.target():
            self.status.setText(tr("Kein Ziel-Raum gewählt – im Ausklappfenster einen Raum anklicken"))
            return
        def work():
            index = start_index
            try:
                meta = server.metadata(c.id)
                if index is None:  # zufälliger Einstieg
                    total = server.browse(c.id, 0, 1).total
                    index = random.randrange(total) if total > 0 else 0
            except Exception:  # noqa: BLE001
                meta, index = "", 0
            self._meta_ready.emit(container_uri(server.udn, c.id, index), meta, shuffle)

        if self.run_async:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()
