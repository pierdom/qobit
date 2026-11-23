from __future__ import annotations

import contextlib
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
from .artist_detail import LIBRARY_TILE_MIN_W, AlbumCard, AlbumGrid, ArtistHeader

if TYPE_CHECKING:
    from ..app import QobitApp

_SORT_OPTIONS: list[tuple[str, str]] = [
    ("favorited_at", "Date Added"),
    ("artist", "Artist"),
    ("title", "Album"),
    ("year", "Year"),
]
_SORT_KEYS = [k for k, _ in _SORT_OPTIONS]


class AlbumsView(TypeToFilter, Widget):
    BINDINGS = [
        Binding("s", "cycle_sort", "Sort"),
        Binding("r", "toggle_reverse", "Rev"),
        Binding("/", "start_filter", "Filter"),
    ]

    DEFAULT_CSS = """
    AlbumsView {
        height: 1fr;
        layout: vertical;
    }
    AlbumsView ContentSwitcher { height: 1fr; }
    AlbumsView #albums-grid-view { height: 1fr; }
    AlbumsView AlbumGrid {
        height: 1fr;
        grid-rows: 5;  /* GRID_CARD_IMG_H */
        grid-gutter: 1 2;  /* same gaps as the Artists and Playlists grids */
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-title-style: bold;
        border-subtitle-color: $qobit-muted;
        border-subtitle-align: right;
        padding: 0 1 0 1;
    }
    AlbumsView AlbumGrid:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    """

    _album_view_active: bool = False
    _loaded: bool = False
    _reselect: str | None = None  # album id to put the cursor back on
    _sort_key: str = "favorited_at"
    _sort_reverse: bool = True
    _render_version: int = 0
    _albums: list[Album]
    _filter_timer: Timer | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._albums = []

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="albums-grid-view", id="albums-switcher"):
            with Vertical(id="albums-grid-view"):
                yield AlbumGrid(id="fav-albums-grid", tile_min_width=LIBRARY_TILE_MIN_W)
            with Vertical(id="albums-album-view"):
                yield Breadcrumb()
                yield ArtistHeader()
                yield AlbumDetailPanel(id="album-panel")

    def on_mount(self) -> None:
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        grid.border_title = readout("Favourite Albums")
        self._update_subtitle()

    def nav_levels(self) -> list[str]:
        """Levels below the tab, for the breadcrumb trail (see nav.py)."""
        if not self._album_view_active:
            return []
        album = self.query_one("#album-panel", AlbumDetailPanel).album
        return [album.title if album else "Album"]

    def nav_to(self, depth: int) -> None:
        if depth == 0 and self._album_view_active:
            self._show_grid()

    def activate(self) -> None:
        """Called by QobitApp when this tab becomes active: first load, focus."""
        if not self._loaded:
            self._loaded = True
            self._load()
        if self._album_view_active:
            focus_later(self, self.query_one("#album-panel", AlbumDetailPanel).focus_tracklist)
        else:
            focus_later(self, self.query_one("#fav-albums-grid", AlbumGrid).focus)

    # ── vertical responsiveness ────────────────────────────────────────────────

    def on_album_detail_panel_resized(self, _: AlbumDetailPanel.Resized) -> None:
        fit_artist_header(
            self.query_one(ArtistHeader), self.query_one("#album-panel", AlbumDetailPanel)
        )

    # ── filter ───────────────────────────────────────────────────────────────

    def _filter_allowed(self) -> bool:
        return not self._album_view_active

    def can_navigate_back(self) -> bool:
        return self._album_view_active or self._filter_active or bool(self._filter_query)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Sort and filter act on the grid: hide them while an album is open.
        if action in ("cycle_sort", "toggle_reverse", "start_filter"):
            return not self._album_view_active
        return True

    def action_navigate_back(self) -> bool:
        if self._album_view_active:
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
        if self._filter_active or self._album_view_active:
            return
        idx = (_SORT_KEYS.index(self._sort_key) + 1) % len(_SORT_KEYS)
        self._sort_key = _SORT_KEYS[idx]
        self._sort_reverse = self._sort_key == "favorited_at"
        self._apply_sort()

    def action_toggle_reverse(self) -> None:
        if self._filter_active or self._album_view_active:
            return
        self._sort_reverse = not self._sort_reverse
        self._apply_sort()

    def _update_subtitle(self) -> None:
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        total = len(self._albums)
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

    def _sorted_albums(self) -> list[Album]:
        def key(a: Album) -> object:
            if self._sort_key == "favorited_at":
                return a.favorited_at or 0
            if self._sort_key == "artist":
                return (a.artist or "").lower()
            if self._sort_key == "title":
                return (a.title or "").lower()
            if self._sort_key == "year":
                return a.year or 0
            return 0

        return sorted(self._albums, key=key, reverse=self._sort_reverse)

    def _schedule_filter(self) -> None:
        """Coalesce a burst of filter keystrokes into a single visibility pass."""
        if self._filter_timer is not None:
            self._filter_timer.stop()
        self._filter_timer = self.set_timer(0.12, self._apply_filter)

    def _apply_filter(self) -> None:
        if self._filter_timer is not None:
            self._filter_timer.stop()
            self._filter_timer = None
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        q = self._filter_query.lower()
        if not q:
            ids: set[str] | None = None
        else:
            ids = {
                a.id
                for a in self._albums
                if q in (a.title or "").lower() or q in (a.artist or "").lower()
            }
        any_visible = grid.filter_cards(ids)
        self._set_empty(grid, not any_visible and bool(self._albums))

    def _set_empty(self, grid: AlbumGrid, show: bool) -> None:
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
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        grid._cursor = -1
        self._mount_cards(self._sorted_albums(), self._render_version)

    @work
    async def _mount_cards(self, albums: list[Album], version: int) -> None:
        if version != self._render_version:
            return
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        await grid.clear_cards()
        if version != self._render_version:
            return
        if not albums:
            await grid.mount(status_line("No favourite albums yet."))
            return
        await grid.set_cards([AlbumCard(a, show_artist=True) for a in albums])
        self._apply_filter()
        if (keep := self._reselect) is not None:
            self._reselect = None
            await grid.select_where(lambda c: isinstance(c, AlbumCard) and c._album.id == keep)

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        grid.border_subtitle = "loading…"
        try:
            self._albums = list(await app.ensure_favorite_albums())
        except Exception as e:
            grid.border_subtitle = ""
            await grid.mount(status_line(str(e), error=True))
            return
        self._update_subtitle()
        self._render_grid()

    def favorites_changed(self) -> None:
        """The favourite albums changed elsewhere (the ``i`` menu, or a
        background refresh of the library): redraw the grid from the app's
        cache, the cursor on the album it was on. Before the first load
        there's nothing to redraw; the load reads the cache anyway."""
        app: QobitApp = self.app  # type: ignore[assignment]
        if self._loaded and app.favorite_albums is not None:
            card = self.query_one("#fav-albums-grid", AlbumGrid).selected_card()
            self._reselect = card._album.id if isinstance(card, AlbumCard) else None
            self._albums = list(app.favorite_albums)
            self._update_subtitle()
            self._render_grid()

    @work(exclusive=True, group="_load_artist_info")  # a newer request cancels a stale one
    async def _load_artist_info(self, artist_id: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            artist = Artist.from_api(await app._client.get_artist_detail(artist_id))
        except Exception:
            return
        if not self.is_attached:
            return
        with contextlib.suppress(Exception):
            self.query_one(ArtistHeader).populate(artist)

    # ── navigation ───────────────────────────────────────────────────────────

    def _open_album(self, album: Album) -> None:
        self._filter_active = False  # exit typing mode; query persists for return

        panel = self.query_one("#album-panel", AlbumDetailPanel)
        panel.load(album)

        header = self.query_one(ArtistHeader)
        header.set_loading(album.artist or "Artist")
        if album.artist_id:
            self._load_artist_info(album.artist_id)

        self.query_one("#albums-switcher", ContentSwitcher).current = "albums-album-view"
        self._album_view_active = True
        # After the switch is laid out: focused at once, the call could be
        # ignored (not displayed yet) with the grid hidden, leaving nothing
        # focused on a busy machine.
        focus_later(self, panel.focus_tracklist)
        self.app.nav_changed()  # type: ignore[attr-defined]

    def _show_grid(self) -> None:
        self.query_one("#albums-switcher", ContentSwitcher).current = "albums-grid-view"
        self._album_view_active = False
        self.query_one("#fav-albums-grid", AlbumGrid).focus()
        self.app.nav_changed()  # type: ignore[attr-defined]

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track, queue=event.queue)
