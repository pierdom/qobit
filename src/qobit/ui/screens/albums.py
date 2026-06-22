from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Label

from ...qobuz.models import Album, Artist
from .album_detail import AlbumDetailPanel
from .artist_detail import AlbumCard, AlbumGrid, ArtistHeader

if TYPE_CHECKING:
    from ..app import QobitApp

_SORT_OPTIONS: list[tuple[str, str]] = [
    ("favorited_at", "Date Added"),
    ("artist", "Artist"),
    ("title", "Album"),
    ("year", "Year"),
]
_SORT_KEYS = [k for k, _ in _SORT_OPTIONS]

# Only mount this many cards at once.  The layout pass Textual runs when
# switching to this tab is O(mounted widgets); keeping the tree small keeps
# transitions snappy.  Mount in batches so the event loop stays responsive
# during the initial load.
_MAX_GRID = 200
_BATCH = 50

# Vertical-responsive thresholds for the album detail page (AlbumsView height).
# As the page gets shorter it progressively reclaims/densifies space: hide the
# artist header, then halve the album art, then show the tracklist in 2 columns.
_HIDE_ARTIST_BELOW = 32
_SMALL_ART_BELOW = 24
_TWO_COL_BELOW = 17


class AlbumsView(Widget):
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
        grid-rows: 6;
        grid-gutter: 1 4;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
        border-subtitle-color: $accent 40%;
        border-subtitle-align: right;
        padding: 0 1 0 1;
    }
    AlbumsView AlbumGrid:focus {
        border: round $accent;
        border-title-color: $accent;
        border-subtitle-color: $accent;
    }
    AlbumsView #album-breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $boost;
    }
    AlbumsView #album-breadcrumb:hover {
        color: $text;
        background: $panel;
    }
    """

    _album_view_active: bool = False
    _loaded: bool = False
    _sort_key: str = "favorited_at"
    _sort_reverse: bool = True
    _filter_active: bool = False
    _filter_query: str = ""
    _render_version: int = 0
    _albums: list[Album]
    _filter_timer: Timer | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._albums = []

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="albums-grid-view", id="albums-switcher"):
            with Vertical(id="albums-grid-view"):
                yield AlbumGrid(id="fav-albums-grid", tile_min_width=42)
            with Vertical(id="albums-album-view"):
                yield Label("← Favourite Albums", id="album-breadcrumb")
                yield ArtistHeader()
                yield AlbumDetailPanel(id="album-panel")

    def on_mount(self) -> None:
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        grid.border_title = "Favourite Albums"
        self._update_subtitle()

    def on_show(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._load()
        if self._album_view_active:
            self.call_after_refresh(
                self.query_one("#album-panel", AlbumDetailPanel).focus_tracklist
            )
        else:
            self.call_after_refresh(self.query_one("#fav-albums-grid", AlbumGrid).focus)

    # ── vertical responsiveness ────────────────────────────────────────────────

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive(event.size.height)

    def _apply_responsive(self, height: int) -> None:
        self.query_one(ArtistHeader).display = height >= _HIDE_ARTIST_BELOW
        self.query_one("#album-panel", AlbumDetailPanel).set_compact(
            small_art=height < _SMALL_ART_BELOW,
            two_col=height < _TWO_COL_BELOW,
        )

    # ── filter ───────────────────────────────────────────────────────────────

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

    def action_start_filter(self) -> None:
        if self._album_view_active:
            return
        self._filter_active = True
        self._update_subtitle()

    def on_key(self, event: events.Key) -> None:
        if not self._filter_active or self._album_view_active:
            return
        if event.is_printable and event.character:
            self._filter_query += event.character
            event.stop()
            self._update_subtitle()
            self._schedule_filter()
        elif event.key in ("backspace", "ctrl+h"):
            if self._filter_query:
                self._filter_query = self._filter_query[:-1]
                event.stop()
                self._update_subtitle()
                self._schedule_filter()

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
            grid.border_subtitle = f"/ {self._filter_query}_"
        elif self._filter_query:
            grid.border_subtitle = f"⌕ {self._filter_query}"
        else:
            arrow = "↓" if self._sort_reverse else "↑"
            label = dict(_SORT_OPTIONS)[self._sort_key]
            suffix = f"  ·  {_MAX_GRID} of {total}" if total > _MAX_GRID else ""
            grid.border_subtitle = f"{arrow} {label}{suffix}"

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
            grid.mount(Label("[dim]No matches.[/dim]", id="grid-empty", markup=True))
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
        await grid.remove_children()
        if version != self._render_version:
            return
        if not albums:
            await grid.mount(Label("[dim]No favourite albums yet.[/dim]", markup=True))
            return
        shown = albums[:_MAX_GRID]
        for i in range(0, len(shown), _BATCH):
            if version != self._render_version:
                return
            await grid.mount(*[AlbumCard(a, show_artist=True) for a in shown[i : i + _BATCH]])
        self._apply_filter()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        try:
            items = await app._client.get_all_favorite_albums()
        except Exception as e:
            await grid.mount(Label(f"[red]{e}[/red]", markup=True))
            return
        self._albums = [Album.from_api(raw) for raw in items]
        self._render_grid()

    @work
    async def _load_artist_info(self, artist_id: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        artist = Artist.from_api(await app._client.get_artist_detail(artist_id))
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
        self._apply_responsive(self.size.height)
        panel.focus_tracklist()

    def _show_grid(self) -> None:
        self.query_one("#albums-switcher", ContentSwitcher).current = "albums-grid-view"
        self._album_view_active = False
        self.query_one("#fav-albums-grid", AlbumGrid).focus()

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track, queue=event.queue)

    @on(events.Click, "#album-breadcrumb")
    def _on_breadcrumb_click(self) -> None:
        self._show_grid()
