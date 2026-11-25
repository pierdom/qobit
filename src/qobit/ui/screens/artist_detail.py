from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Label, ListItem, ListView
from textual_image.widget import TGPImage

from ...qobuz.client import QobuzError
from ...qobuz.models import Album, Artist, Track
from .._images import fetch_image
from .._now_playing import (
    GUTTER_ICON,
    GUTTER_NUM,
    ICON_FAV,
    NowPlayingRowMixin,
    heart_suffix,
    meta_indent,
    playing_content,
)
from .._utils import markup_escape, qapp, readout, strip_html
from ..nav import QobitFooter, page_header
from ..themes import HEART, theme_color
from ..widgets.art import Art, square_width
from ..widgets.lists import TrackListView
from ..widgets.transport import TransportBar
from .album_detail import AlbumDetailPanel
from .cards import AlbumCard, AlbumGrid, ArtistCard, ArtistGrid
from .search import ICON_TRACK

if TYPE_CHECKING:
    pass


class ArtistTrackRow(NowPlayingRowMixin, ListItem):
    DEFAULT_CSS = """
    ArtistTrackRow { height: 2; padding: 0 1; }
    ArtistTrackRow Label { width: 1fr; }
    ArtistTrackRow .row-meta { color: $qobit-muted; }
    """

    def __init__(self, track: Track, number: int | None, favorite: bool = False) -> None:
        """``number`` None: an unnumbered row (♪), as in the favourites list."""
        super().__init__()
        self.track = track
        self._number = number
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = heart_suffix(self._favorite)
        lead = f"{self._number}." if self._number else ICON_TRACK  # numbered lists: no ♪
        return playing_content(
            lead, t.display_title, self._is_playing, self._is_paused, heart, width=self._width
        )

    @property
    def _width(self) -> int:
        return GUTTER_NUM if self._number else GUTTER_ICON

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="row-title")
        yield Label(
            f"{meta_indent(self._width)}{t.album}  ·  {t.duration_str}",
            classes="row-meta",
            markup=False,
        )

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self._refresh_primary()


# Both lists side by side from this width; below it, one panel and a switch.
ARTIST_TRACKS_COLUMNS_MIN_W = 120
# The header lists a few similar artists beside the bio from this terminal width.
ARTIST_HEADER_SIMILAR_MIN_W = 128

# Panels that hold two views (Top Tracks / Your favourites, Albums / Similar)
# name both in the border title: the shown one underlined, like the active tab
# in the top bar; `t` or a click on a name switches. (Not `dim` for the other:
# on an unfocused panel's 40% title it became unreadable.)
_SWITCH_SEP = "  │  "
SWITCH_HINT = "t or click the title to switch"


def switch_title(labels: list[str], active: int) -> str:
    return _SWITCH_SEP.join(
        f"[underline]{label}[/underline]" if i == active else f"[not bold]{label}[/not bold]"
        for i, label in enumerate(labels)
    )


def clicked_label(panel: Widget, event: events.Click, labels: list[str]) -> int | None:
    """Which name in ``panel``'s switch title was clicked, if any. The title
    starts 3 cells in: corner, one rule, one space."""
    if event.screen_y != panel.region.y:
        return None
    x = event.screen_x - (panel.region.x + 3)
    start = 0
    for i, label in enumerate(labels):
        if start <= x < start + len(label):
            return i
        start += len(label) + len(_SWITCH_SEP)
    return None


class _TitleSwitch:
    """A panel with two views named in its border title: a click on the
    hidden view's name runs ``action_switch``. Hosts say which panel shows
    the title, its labels and the active index (None: not switching)."""

    def _switch_state(self) -> tuple[Widget, list[str], int] | None:
        raise NotImplementedError

    def action_switch(self) -> None:
        raise NotImplementedError

    def on_click(self, event: events.Click) -> None:
        state = self._switch_state()
        if state is None:
            return
        panel, labels, active = state
        picked = clicked_label(panel, event, labels)
        if picked is not None and picked != active:
            event.stop()
            self.action_switch()


class ArtistTracks(_TitleSwitch, Widget):
    """An artist's Top Tracks and the user's favourite tracks by that artist.

    Wide: two columns. Narrow: one panel showing either list; `t`, or ←/→,
    switches. No favourites by this artist: Top Tracks alone. Shared by the
    artist page and the Artists tab. Call ``load(artist_id)``."""

    BINDINGS = [
        Binding("t", "switch", "Top / Favs", show=False),
        Binding("left", "switch", show=False),
        Binding("right", "switch", show=False),
    ]

    DEFAULT_CSS = """
    ArtistTracks { layout: horizontal; height: 1fr; }
    ArtistTracks TrackListView {
        width: 1fr;
        height: 1fr;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-subtitle-color: $qobit-muted;
        border-title-style: bold;
    }
    ArtistTracks TrackListView:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    ArtistTracks.-columns #artist-favs { margin-left: 1; }
    """

    _artist_id: str | None = None
    _showing: str = "top"  # in switch mode: "top" | "favs"
    _fav_ids: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        yield TrackListView(id="top-tracks")
        yield TrackListView(id="artist-favs")

    def on_mount(self) -> None:
        app = self.app
        self.watch(app, "now_playing", lambda _: self._mark_playing(), init=False)
        self.watch(app, "is_paused", lambda _: self._mark_playing(), init=False)
        # toggle_favorite bumps queue_version: refresh the favourites column
        # and the hearts when a track is (un)favourited anywhere.
        self.watch(app, "queue_version", lambda _: self._sync_favourites(), init=False)
        self._layout()

    def load(self, artist_id: str) -> None:
        self._artist_id = artist_id
        self._showing = "top"
        self._fav_ids = ()
        self._load()

    @work(exclusive=True, group="_load")  # a newer artist cancels a stale load
    async def _load(self) -> None:
        app = qapp(self)
        top = self.query_one("#top-tracks", TrackListView)
        await top.clear()
        await self.query_one("#artist-favs", TrackListView).clear()
        self._layout()
        artist_id = self._artist_id
        try:
            items = await app._client.get_artist_top_tracks(artist_id or "")
        except QobuzError:
            items = []
        fav_ids = await app.library.ensure_track_ids()
        if not self.is_attached or artist_id != self._artist_id:
            return
        rows = [
            ArtistTrackRow(t := Track.from_api(raw), i, str(t.id) in fav_ids)
            for i, raw in enumerate(items, 1)
        ]
        await top.mount(*rows)
        await self._refresh_favs()

    def _favourites(self) -> list[Track]:
        app = qapp(self)
        if self._artist_id is None:
            return []
        tracks = [t for t in (app.library.tracks or []) if t.by_artist(self._artist_id)]
        return sorted(tracks, key=lambda t: t.favorited_at or 0, reverse=True)

    async def _refresh_favs(self) -> None:
        app = qapp(self)
        with contextlib.suppress(Exception):
            await app.library.ensure_tracks()
        tracks = self._favourites()
        self._fav_ids = tuple(t.id for t in tracks)
        favs = self.query_one("#artist-favs", TrackListView)
        await favs.clear()
        await favs.mount(*[ArtistTrackRow(t, None, favorite=True) for t in tracks])
        self._mark_playing()
        self._layout()

    def _sync_favourites(self) -> None:
        """After a (un)favourite anywhere: fix the hearts, and rebuild the
        favourites list only if its tracks changed."""
        if self._artist_id is None or not self.is_attached:
            return
        app = qapp(self)
        ids = app.library.track_ids or set()
        for row in self.query_one("#top-tracks", TrackListView).query(ArtistTrackRow):
            row.set_favorite(str(row.track.id) in ids)
        if tuple(t.id for t in self._favourites()) != self._fav_ids:
            self.run_worker(self._refresh_favs(), group="_refresh_favs", exclusive=True)

    def _mark_playing(self) -> None:
        app = qapp(self)
        now_id = getattr(app.now_playing, "id", None)
        for row in self.query(ArtistTrackRow):
            row.set_playing(row.track.id == now_id, paused=app.is_paused)

    # ── layout ───────────────────────────────────────────────────────────────

    def on_resize(self) -> None:
        self._layout()

    @property
    def _columns(self) -> bool:
        return bool(self._fav_ids) and self.size.width >= ARTIST_TRACKS_COLUMNS_MIN_W

    def _layout(self) -> None:
        top = self.query_one("#top-tracks", TrackListView)
        favs = self.query_one("#artist-favs", TrackListView)
        n = len(self._fav_ids)
        columns = self._columns
        self.set_class(columns, "-columns")
        top.display = columns or not n or self._showing == "top"
        favs.display = bool(n) and (columns or self._showing == "favs")
        if columns or not n:
            top.border_title, favs.border_title = self._labels
        else:  # one panel: name both lists, the hidden one dimmed
            title = switch_title(self._labels, 0 if self._showing == "top" else 1)
            (top if self._showing == "top" else favs).border_title = title
        top.border_subtitle = favs.border_subtitle = "" if columns or not n else SWITCH_HINT

    @property
    def _labels(self) -> list[str]:
        return [readout("Top Tracks"), readout(f"Your favourites · {len(self._fav_ids)}")]

    def _switch_state(self) -> tuple[Widget, list[str], int] | None:
        if not self._fav_ids or self._columns:
            return None
        top = self._showing == "top"
        panel = self.query_one("#top-tracks" if top else "#artist-favs")
        return panel, self._labels, 0 if top else 1

    def action_switch(self) -> None:
        if not self._fav_ids:
            return
        if self._columns:  # both visible: move focus across
            target = (
                "#artist-favs"
                if self.screen.focused is self.query_one("#top-tracks")
                else "#top-tracks"
            )
            self.query_one(target, TrackListView).focus()
            return
        self._showing = "favs" if self._showing == "top" else "top"
        self._layout()
        self.query_one("#top-tracks" if self._showing == "top" else "#artist-favs").focus()

    # ── play ─────────────────────────────────────────────────────────────────

    @on(ListView.Selected)
    def _on_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, ArtistTrackRow):
            return
        event.stop()
        rows = list(event.list_view.query(ArtistTrackRow))
        idx = rows.index(event.item)
        app = qapp(self)
        app.play_track(event.item.track, queue=[r.track for r in rows[idx + 1 :]])
        self.call_after_refresh(event.list_view.focus)


class ArtistReleases(_TitleSwitch, Widget):
    """The artist's Albums & EPs, and their Similar artists in the same panel:
    `t` switches (the arrows move the grid cursor). Shown only when Qobuz has
    similar artists. The full list loads the first time it's opened. The
    album grid keeps the id the host gives it; the host fills it."""

    BINDINGS = [Binding("t", "switch", "Albums / Similar", show=False)]

    DEFAULT_CSS = """
    ArtistReleases { height: 1fr; }
    ArtistReleases #similar-artists {
        display: none;
        height: 1fr;
        grid-rows: 5;  /* GRID_CARD_IMG_H */
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-title-style: bold;
        border-subtitle-color: $qobit-muted;
    }
    ArtistReleases #similar-artists:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    """

    def __init__(self, albums_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._albums_id = albums_id
        self._artist_id: str | None = None
        self._showing = "albums"
        self._has_similar = False
        self._similar_loaded = False
        self._similar_count: int | None = None

    def compose(self) -> ComposeResult:
        yield AlbumGrid(id=self._albums_id)
        yield ArtistGrid(id="similar-artists")

    @property
    def albums(self) -> AlbumGrid:
        return self.query_one(f"#{self._albums_id}", AlbumGrid)

    def on_mount(self) -> None:
        self._layout()

    async def set_artist(self, artist_id: str) -> None:
        self._artist_id = artist_id
        self._showing = "albums"
        self._has_similar = self._similar_loaded = False
        self._similar_count = None
        await self.query_one("#similar-artists", ArtistGrid).clear_cards()
        self._layout()
        self._check_similar(artist_id)

    @work(exclusive=True, group="_check_similar")  # a newer artist cancels a stale one
    async def _check_similar(self, artist_id: str) -> None:
        app = qapp(self)
        try:
            items, _ = await app._client.get_similar_artists_preview(artist_id)
        except QobuzError:
            return
        if self.is_attached and artist_id == self._artist_id:
            self._has_similar = bool(items)
            self._layout()

    def _layout(self) -> None:
        albums = self.albums
        similar = self.query_one("#similar-artists", ArtistGrid)
        on_similar = self._has_similar and self._showing == "similar"
        albums.display = not on_similar
        similar.display = on_similar
        if not self._has_similar:
            albums.border_title, albums.border_subtitle = readout("Albums & EPs"), ""
        elif on_similar:
            similar.border_title = switch_title(self._labels, 1)
            similar.border_subtitle = SWITCH_HINT
        else:
            albums.border_title = switch_title(self._labels, 0)
            albums.border_subtitle = SWITCH_HINT

    @property
    def _labels(self) -> list[str]:
        count = f"  ·  {self._similar_count}" if self._similar_count is not None else ""
        return [readout("Albums & EPs"), readout(f"Similar artists{count}")]

    def _switch_state(self) -> tuple[Widget, list[str], int] | None:
        if not self._has_similar:
            return None
        on_similar = self._showing == "similar"
        panel = self.query_one("#similar-artists") if on_similar else self.albums
        return panel, self._labels, int(on_similar)

    def action_switch(self) -> None:
        if not self._has_similar:
            return
        self._showing = "similar" if self._showing == "albums" else "albums"
        self._layout()
        if self._showing == "similar":
            self.query_one("#similar-artists", ArtistGrid).focus()
            if not self._similar_loaded:
                self._similar_loaded = True
                self._load_similar(self._artist_id or "")
        else:
            self.albums.focus()

    @work(exclusive=True, group="_load_similar")
    async def _load_similar(self, artist_id: str) -> None:
        app = qapp(self)
        grid = self.query_one("#similar-artists", ArtistGrid)
        grid.border_subtitle = "loading…"
        try:
            items = await app._client.get_similar_artists(artist_id)
        except QobuzError:
            self._similar_loaded = False  # let `t` retry
            grid.border_subtitle = "couldn't load"
            return
        if not self.is_attached or artist_id != self._artist_id:
            return
        followed = {a.id for a in (app.library.artists or [])}
        artists = [Artist.from_api(raw) for raw in items]
        self._similar_count = len(artists)
        await grid.set_cards([ArtistCard(a, followed=a.id in followed) for a in artists])
        self._layout()

    @on(ArtistCard.Selected)
    def _on_similar_selected(self, event: ArtistCard.Selected) -> None:
        event.stop()
        app = qapp(self)
        app.open_artist(event.artist.id)


class ArtistHeader(Widget):
    """Reusable artist image + biography header.

    Call ``show_loading(name)`` immediately when switching context, then
    ``populate(artist)`` once the API data arrives.
    """

    DEFAULT_CSS = """
    ArtistHeader {
        height: 8;
        padding: 1 0 0 0;
        layout: horizontal;
    }
    ArtistHeader .ah-image {
        width: 16;
        height: 7;
        margin-right: 1;
        border: round $accent 40%;
    }
    ArtistHeader .ah-bio-section {
        width: 1fr;
        height: 7;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-subtitle-color: $qobit-muted;
        border-title-style: bold;
    }
    ArtistHeader .ah-bio-section:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    ArtistHeader .ah-bio {
        width: 1fr;
        color: $qobit-muted;
    }
    ArtistHeader .ah-similar {
        display: none;
        width: 32;
        height: 7;
        margin-left: 1;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-title-style: bold;
        border-subtitle-color: $qobit-muted;
    }
    ArtistHeader .ah-similar:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    ArtistHeader.-similar .ah-similar { display: block; }
    ArtistHeader .ah-similar ListItem { height: 1; padding: 0 1; }
    """

    def __init__(self, *, similar: bool = False, **kwargs: Any) -> None:
        """``similar``: on wide terminals, list a few similar artists beside
        the bio (artist pages; not the album pages that reuse this header)."""
        super().__init__(**kwargs)
        self._similar = similar
        self._similar_items: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Art(classes="ah-image")
        with VerticalScroll(classes="ah-bio-section"):
            yield Label("", classes="ah-bio")
        yield ListView(classes="ah-similar")

    def on_mount(self) -> None:
        square_width(self.query_one(TGPImage), 7)
        self.watch(self.app, "theme", self._on_theme, init=False)

    artist: Artist | None = None
    """The artist shown, once ``populate`` has run."""

    def show_loading(self, name: str = "Loading…") -> None:
        self.artist = None
        self._similar_items = []
        self._layout_similar()
        self.query_one(".ah-bio-section").border_title = markup_escape(readout(name))
        art = self.query_one(Art)
        art.image = None  # not the previous artist's photo under this name
        art.placeholder = name
        self.query_one(".ah-bio", Label).update("")

    def populate(self, artist: Artist) -> None:
        self.artist = artist
        self.refresh_heart()
        self._load_heart(artist.id)
        self.query_one(Art).placeholder = artist.name
        if artist.biography:
            # API HTML (<p>, <br/>, entities) as plain text, like the Now
            # Playing bio; then escaped, the Label takes markup.
            self.query_one(".ah-bio", Label).update(markup_escape(strip_html(artist.biography)))
        if artist.image_url:
            self._fetch_image(artist.image_url)
        if self._similar:
            self._load_similar(artist.id)

    def refresh_heart(self) -> None:
        """The bio panel's title: the name, with a ♥ when followed. A border
        title doesn't resolve $variables, so the colour is the theme's value
        (redrawn on a theme change)."""
        if self.artist is None:
            return
        app = qapp(self)
        title = markup_escape(readout(self.artist.name))
        if app.library.is_followed_artist(self.artist.id):
            title += f"  [{theme_color(self.app, HEART)}]{ICON_FAV}[/]"
        self.query_one(".ah-bio-section").border_title = title

    @work(exclusive=True, group="_load_heart")  # a newer artist cancels a stale one
    async def _load_heart(self, artist_id: str) -> None:
        app = qapp(self)
        try:
            await app.library.ensure_artists()
        except QobuzError:
            return
        if self.is_attached and self.artist is not None and self.artist.id == artist_id:
            self.refresh_heart()

    def _on_theme(self, _: object) -> None:
        # theme fires before theme_variables is recomputed (see CLAUDE.md).
        self.call_after_refresh(self.refresh_heart)

    @work(exclusive=True, group="_load_similar")  # a newer artist cancels a stale one
    async def _load_similar(self, artist_id: str) -> None:
        app = qapp(self)
        try:
            items, more = await app._client.get_similar_artists_preview(artist_id)
        except QobuzError:
            return
        if not self.is_attached or self.artist is None or self.artist.id != artist_id:
            return
        self._similar_items = items
        lv = self.query_one(".ah-similar", ListView)
        await lv.clear()
        await lv.mount(*[ListItem(Label(it["name"], markup=False)) for it in items])
        lv.border_title = readout("Similar")
        lv.border_subtitle = "more below (t)" if more else ""
        self._layout_similar()

    def on_resize(self) -> None:
        self._layout_similar()

    def _layout_similar(self) -> None:
        wide = self.app.size.width >= ARTIST_HEADER_SIMILAR_MIN_W
        self.set_class(bool(self._similar_items) and wide, "-similar")

    @on(ListView.Selected, ".ah-similar")
    def _on_similar_selected(self, event: ListView.Selected) -> None:
        event.stop()
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._similar_items):
            app = qapp(self)
            app.open_artist(self._similar_items[idx]["id"])

    @work(exclusive=True, group="_fetch_image")  # a newer request cancels a stale one
    async def _fetch_image(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_attached:
            with contextlib.suppress(NoMatches):
                self.query_one(TGPImage).image = img


class ArtistScreen(Screen):
    """An artist page pushed over the current one (Search, `g`, `i`,
    Similar). Two levels: the artist, and an album opened from it."""

    DEFAULT_CSS = """
    ArtistScreen { layout: vertical; }

    ArtistScreen #main-content {
        height: 1fr;
        margin: 0;
        padding: 0;
    }

    ArtistScreen #artist-view {
        height: 1fr;
        layout: vertical;
        margin: 0;
        padding: 0;
    }

    ArtistScreen #albums {
        height: 1fr;
        margin: 0;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-subtitle-color: $qobit-muted;
        border-title-style: bold;
    }

    ArtistScreen #albums:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }

    ArtistScreen #album-view {
        height: 1fr;
        layout: vertical;
        margin: 0;
        padding: 0;
    }

    ArtistScreen TransportBar {
        margin: 0 0 1 0;
    }
    """

    def __init__(self, artist_id: str) -> None:
        super().__init__()
        self._artist_id = artist_id
        self._album_view_active = False

    def compose(self) -> ComposeResult:
        yield from page_header(qapp(self))
        yield ArtistHeader(similar=True)
        with ContentSwitcher(initial="artist-view", id="main-content"):
            with Vertical(id="artist-view"):
                yield ArtistTracks()
                yield ArtistReleases("albums")
            with Vertical(id="album-view"):
                yield AlbumDetailPanel(id="album-panel")
        yield TransportBar()
        yield QobitFooter()

    def on_mount(self) -> None:
        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        self.query_one(ArtistHeader).show_loading()
        self.run_worker(self.query_one(ArtistReleases).set_artist(self._artist_id))
        self._load_detail()
        self.query_one(ArtistTracks).load(self._artist_id)

    @property
    def page_artist(self) -> Artist | None:
        """The artist this page is about: what the ``i`` menu's follow acts on."""
        return self.query_one(ArtistHeader).artist

    def nav_levels(self) -> list[str]:
        artist = self.page_artist
        levels = [artist.name if artist else "Artist"]
        if self._album_view_active:
            album = self.query_one("#album-panel", AlbumDetailPanel).album
            levels.append(album.title if album else "Album")
        return levels

    def nav_to(self, depth: int) -> None:
        if depth == 0 and self._album_view_active:
            self._show_artist_view()

    def _open_album(self, album: Album) -> None:
        panel = self.query_one("#album-panel", AlbumDetailPanel)
        panel.load(album)
        self.query_one("#main-content", ContentSwitcher).current = "album-view"
        self._album_view_active = True
        # After the switch is laid out: focused at once, the call could be
        # ignored (not displayed yet) with the grid hidden, leaving nothing
        # focused on a busy machine.
        self.call_after_refresh(panel.focus_tracklist)
        qapp(self).nav_changed()

    def _show_artist_view(self) -> None:
        self.query_one("#main-content", ContentSwitcher).current = "artist-view"
        self._album_view_active = False
        self.query_one("#albums", AlbumGrid).focus()
        qapp(self).nav_changed()

    @work
    async def _load_detail(self) -> None:
        app = qapp(self)
        try:
            artist = Artist.from_api(await app._client.get_artist_detail(self._artist_id))
        except Exception:
            return
        if not self.is_attached:
            return
        try:
            self.query_one(ArtistHeader).populate(artist)
            app.nav_changed()  # the trail can name the artist now
            if artist.albums:
                grid = self.query_one("#albums", AlbumGrid)
                await grid.set_cards([AlbumCard(album) for album in artist.albums])
        except NoMatches:
            pass

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_album_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app = qapp(self)
        app.play_track(event.track, queue=event.queue)
