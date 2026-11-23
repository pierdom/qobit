from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import ContentSwitcher

from ...qobuz.models import Album, Artist
from .._filter import TypeToFilter, filter_prompt
from .._utils import focus_later, readout, status_line
from ..nav import Breadcrumb
from .album_detail import AlbumDetailPanel, fit_artist_header
from .artist_detail import (
    LIBRARY_TILE_MIN_W,
    AlbumCard,
    AlbumGrid,
    ArtistCard,
    ArtistGrid,
    ArtistHeader,
    ArtistReleases,
    ArtistTracks,
)

if TYPE_CHECKING:
    from ..app import QobitApp

_SORT_OPTIONS: list[tuple[str, str]] = [
    ("favorited_at", "Date Added"),
    ("name", "Name"),
]
_SORT_KEYS = [k for k, _ in _SORT_OPTIONS]


class ArtistsView(TypeToFilter, Widget):
    BINDINGS = [
        Binding("s", "cycle_sort", "Sort"),
        Binding("r", "toggle_reverse", "Rev"),
        Binding("/", "start_filter", "Filter"),
    ]

    DEFAULT_CSS = """
    ArtistsView {
        height: 1fr;
        layout: vertical;
    }
    ArtistsView ContentSwitcher { height: 1fr; }
    ArtistsView #artists-grid-view { height: 1fr; }
    ArtistsView ArtistGrid {
        height: 1fr;
        grid-rows: 5;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-title-style: bold;
        border-subtitle-color: $qobit-muted;
        border-subtitle-align: right;
        padding: 0 1 0 1;
    }
    ArtistsView ArtistGrid:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    ArtistsView #artist-detail-view { height: 1fr; }
    ArtistsView #artist-content-switcher { height: 1fr; }
    ArtistsView #artist-albums-grid {
        height: 1fr;
        grid-rows: 4;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-subtitle-color: $qobit-muted;
        border-title-style: bold;
        padding: 0 1 0 1;
    }
    ArtistsView #artist-albums-grid:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    """

    _view: str = "grid"  # "grid" | "artist" | "album"
    _loaded: bool = False
    _reselect: str | None = None  # artist id to put the cursor back on
    _sort_key: str = "favorited_at"
    _sort_reverse: bool = True
    _render_version: int = 0
    _artists: list[Artist]
    _current_artist: Artist | None
    _filter_timer: Timer | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._artists = []
        self._current_artist = None

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="artists-grid-view", id="artists-switcher"):
            with Vertical(id="artists-grid-view"):
                yield ArtistGrid(id="fav-artists-grid", tile_min_width=LIBRARY_TILE_MIN_W)
            with Vertical(id="artist-detail-view"):
                yield Breadcrumb()
                yield ArtistHeader(similar=True)
                with ContentSwitcher(
                    initial="artist-tracks-albums-view", id="artist-content-switcher"
                ):
                    with Vertical(id="artist-tracks-albums-view"):
                        yield ArtistTracks()
                        yield ArtistReleases("artist-albums-grid")
                    with Vertical(id="artist-album-detail-view"):
                        yield AlbumDetailPanel(id="album-panel")

    def on_mount(self) -> None:
        self.query_one("#fav-artists-grid", ArtistGrid).border_title = readout("Favourite Artists")
        self._update_subtitle()

    @property
    def page_artist(self) -> Artist | None:
        """The open artist (detail or one of their albums), for the ``i`` menu."""
        return self._current_artist if self._view != "grid" else None

    def nav_levels(self) -> list[str]:
        """Levels below the tab, for the breadcrumb trail (see nav.py)."""
        if self._view == "grid":
            return []
        levels = [self._current_artist.name if self._current_artist else "Artist"]
        if self._view == "album":
            album = self.query_one("#album-panel", AlbumDetailPanel).album
            levels.append(album.title if album else "Album")
        return levels

    def nav_to(self, depth: int) -> None:
        if depth == 0 and self._view != "grid":
            self._show_grid()
        elif depth == 1 and self._view == "album":
            self._show_artist_content()

    def activate(self) -> None:
        """Called by QobitApp when this tab becomes active: first load, focus."""
        if not self._loaded:
            self._loaded = True
            self._load()
        if self._view == "album":
            focus_later(self, self.query_one("#album-panel", AlbumDetailPanel).focus_tracklist)
        elif self._view == "artist":
            focus_later(self, self.query_one("#artist-albums-grid", AlbumGrid).focus)
        else:
            focus_later(self, self.query_one("#fav-artists-grid", ArtistGrid).focus)

    # ── filter ───────────────────────────────────────────────────────────────

    def _filter_allowed(self) -> bool:
        return self._view == "grid"

    def can_navigate_back(self) -> bool:
        return self._view != "grid" or self._filter_active or bool(self._filter_query)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Sort and filter act on the grid: hide them on an artist or album page.
        if action in ("cycle_sort", "toggle_reverse", "start_filter"):
            return self._view == "grid"
        return True

    def action_navigate_back(self) -> bool:
        if self._view == "album":
            self._show_artist_content()
            return True
        if self._view == "artist":
            self._show_grid()
            return True
        if self._filter_active or self._filter_query:
            self._filter_active = False
            self._filter_query = ""
            self._update_subtitle()
            self._apply_filter()
            return True
        return False

    # ── sort ─────────────────────────────────────────────────────────────────

    def action_cycle_sort(self) -> None:
        if self._filter_active or self._view != "grid":
            return
        idx = (_SORT_KEYS.index(self._sort_key) + 1) % len(_SORT_KEYS)
        self._sort_key = _SORT_KEYS[idx]
        self._sort_reverse = self._sort_key == "favorited_at"
        self._apply_sort()

    def action_toggle_reverse(self) -> None:
        if self._filter_active or self._view != "grid":
            return
        self._sort_reverse = not self._sort_reverse
        self._apply_sort()

    def _update_subtitle(self) -> None:
        grid = self.query_one("#fav-artists-grid", ArtistGrid)
        total = len(self._artists)
        if self._filter_active:
            grid.border_subtitle = filter_prompt(self._filter_query)
        elif self._filter_query:
            grid.border_subtitle = f"⌕ {self._filter_query}"
        else:
            arrow = "↓" if self._sort_reverse else "↑"
            label = dict(_SORT_OPTIONS)[self._sort_key]
            grid.border_subtitle = f"{arrow} {label}  ·  {total}"

    def _apply_sort(self) -> None:
        self._update_subtitle()
        self._render_grid()

    # ── data ─────────────────────────────────────────────────────────────────

    def _sorted_artists(self) -> list[Artist]:
        def key(a: Artist) -> object:
            if self._sort_key == "favorited_at":
                return a.favorited_at or 0
            if self._sort_key == "name":
                return a.name.lower()
            return 0

        return sorted(self._artists, key=key, reverse=self._sort_reverse)

    def _schedule_filter(self) -> None:
        """Coalesce a burst of filter keystrokes into a single visibility pass."""
        if self._filter_timer is not None:
            self._filter_timer.stop()
        self._filter_timer = self.set_timer(0.12, self._apply_filter)

    def _apply_filter(self) -> None:
        if self._filter_timer is not None:
            self._filter_timer.stop()
            self._filter_timer = None
        grid = self.query_one("#fav-artists-grid", ArtistGrid)
        q = self._filter_query.lower()
        ids = None if not q else {a.id for a in self._artists if q in a.name.lower()}
        any_visible = grid.filter_cards(ids)
        self._set_empty(grid, not any_visible and bool(self._artists))

    def _set_empty(self, grid: ArtistGrid, show: bool) -> None:
        existing = list(grid.query("#grid-empty"))
        if show and not existing:
            grid.mount(status_line("No matches.", id="grid-empty"))
        elif not show and existing:
            existing[0].remove()

    def _render_grid(self) -> None:
        if self._filter_timer is not None:
            self._filter_timer.stop()
            self._filter_timer = None
        self._render_version += 1
        grid = self.query_one("#fav-artists-grid", ArtistGrid)
        grid._cursor = -1
        self._mount_cards(self._sorted_artists(), self._render_version)

    @work
    async def _mount_cards(self, artists: list[Artist], version: int) -> None:
        if version != self._render_version:
            return
        grid = self.query_one("#fav-artists-grid", ArtistGrid)
        await grid.clear_cards()
        if version != self._render_version:
            return
        if not artists:
            await grid.mount(status_line("No favourite artists yet."))
            return
        await grid.set_cards([ArtistCard(a) for a in artists])
        self._apply_filter()
        if (keep := self._reselect) is not None:
            self._reselect = None
            await grid.select_where(lambda c: isinstance(c, ArtistCard) and c.artist.id == keep)

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        grid = self.query_one("#fav-artists-grid", ArtistGrid)
        grid.border_subtitle = "loading…"
        try:
            self._artists = list(await app.ensure_favorite_artists())
        except Exception as e:
            grid.border_subtitle = ""
            await grid.mount(status_line(str(e), error=True))
            return
        self._update_subtitle()
        self._render_grid()

    def favorites_changed(self) -> None:
        """The followed artists changed elsewhere (the ``i`` menu, or a
        background refresh of the library): redraw the grid from the app's
        cache, the cursor on the artist it was on. Before the first load
        there's nothing to redraw; the load reads the cache anyway."""
        app: QobitApp = self.app  # type: ignore[assignment]
        if self._loaded and app.favorite_artists is not None:
            card = self.query_one("#fav-artists-grid", ArtistGrid).selected_card()
            self._reselect = card.artist.id if isinstance(card, ArtistCard) else None
            self._artists = list(app.favorite_artists)
            self._update_subtitle()
            self._render_grid()

    # ── navigation ───────────────────────────────────────────────────────────

    @work
    async def _open_artist(self, artist: Artist) -> None:
        self._current_artist = artist
        self._filter_active = False  # exit typing mode; query persists for return
        self.query_one(ArtistHeader).set_loading(artist.name)

        grid = self.query_one("#artist-albums-grid", AlbumGrid)
        grid._cursor = -1
        await grid.clear_cards()
        await self.query_one(ArtistReleases).set_artist(artist.id)

        inner_cs = self.query_one("#artist-content-switcher", ContentSwitcher)
        inner_cs.current = "artist-tracks-albums-view"
        self.query_one("#artists-switcher", ContentSwitcher).current = "artist-detail-view"
        self._view = "artist"
        focus_later(self, grid.focus)
        self.app.nav_changed()  # type: ignore[attr-defined]

        self._load_artist_detail(artist.id)
        self.query_one(ArtistTracks).load(artist.id)

    def _open_album(self, album: Album) -> None:
        panel = self.query_one("#album-panel", AlbumDetailPanel)
        panel.load(album)
        inner_cs = self.query_one("#artist-content-switcher", ContentSwitcher)
        inner_cs.current = "artist-album-detail-view"
        self._view = "album"
        # After the switch is laid out: focused at once, the call could be
        # ignored (not displayed yet) with the grid hidden, leaving nothing
        # focused on a busy machine.
        focus_later(self, panel.focus_tracklist)
        self.app.nav_changed()  # type: ignore[attr-defined]

    def on_album_detail_panel_resized(self, _: AlbumDetailPanel.Resized) -> None:
        # The artist's header may give its rows to an open album on a short
        # terminal; it comes back with the artist's own page.
        if self._view == "album":
            fit_artist_header(
                self.query_one(ArtistHeader), self.query_one("#album-panel", AlbumDetailPanel)
            )

    def _show_artist_content(self) -> None:
        self.query_one(ArtistHeader).display = True
        inner_cs = self.query_one("#artist-content-switcher", ContentSwitcher)
        inner_cs.current = "artist-tracks-albums-view"
        self._view = "artist"
        self.query_one("#artist-albums-grid", AlbumGrid).focus()
        self.app.nav_changed()  # type: ignore[attr-defined]

    def _show_grid(self) -> None:
        self.query_one("#artists-switcher", ContentSwitcher).current = "artists-grid-view"
        self._view = "grid"
        self.query_one("#fav-artists-grid", ArtistGrid).focus()
        self.app.nav_changed()  # type: ignore[attr-defined]

    @work(exclusive=True, group="_load_artist_detail")  # a newer request cancels a stale one
    async def _load_artist_detail(self, artist_id: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            artist = Artist.from_api(await app._client.get_artist_detail(artist_id))
        except Exception:
            return
        if not self.is_attached:
            return
        try:
            self.query_one(ArtistHeader).populate(artist)
            if artist.albums:
                grid = self.query_one("#artist-albums-grid", AlbumGrid)
                await grid.set_cards([AlbumCard(album) for album in artist.albums])
        except Exception:
            pass

    @on(ArtistCard.Selected)
    def _on_artist_selected(self, event: ArtistCard.Selected) -> None:
        self._open_artist(event.artist)

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track, queue=event.queue)
