from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Vertical
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


class AlbumsView(Widget):
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
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
        border-subtitle-color: $accent 40%;
        border-subtitle-align: right;
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
    _albums: list[Album]

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._albums = []

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="albums-grid-view", id="albums-switcher"):
            with Vertical(id="albums-grid-view"):
                yield AlbumGrid(id="fav-albums-grid", tile_min_width=33)
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

    def action_navigate_back(self) -> bool:
        if self._album_view_active:
            self._show_grid()
            return True
        return False

    def action_cycle_sort(self) -> None:
        idx = (_SORT_KEYS.index(self._sort_key) + 1) % len(_SORT_KEYS)
        self._sort_key = _SORT_KEYS[idx]
        self._sort_reverse = self._sort_key == "favorited_at"
        self._apply_sort()

    def action_toggle_reverse(self) -> None:
        self._sort_reverse = not self._sort_reverse
        self._apply_sort()

    def _update_subtitle(self) -> None:
        arrow = "↓" if self._sort_reverse else "↑"
        label = dict(_SORT_OPTIONS)[self._sort_key]
        self.query_one("#fav-albums-grid", AlbumGrid).border_subtitle = f"{arrow} {label}"

    def _apply_sort(self) -> None:
        self._update_subtitle()
        self._render_grid()

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

    def _render_grid(self) -> None:
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        grid._cursor = -1
        self._mount_cards(self._sorted_albums())

    @work
    async def _mount_cards(self, albums: list[Album]) -> None:
        grid = self.query_one("#fav-albums-grid", AlbumGrid)
        await grid.remove_children()
        if not albums:
            await grid.mount(Label("[dim]No favourite albums yet.[/dim]", markup=True))
            return
        await grid.mount(*[AlbumCard(album, show_artist=True) for album in albums])

    def _open_album(self, album: Album) -> None:
        panel = self.query_one("#album-panel", AlbumDetailPanel)
        panel.load(album)

        header = self.query_one(ArtistHeader)
        header.set_loading(album.artist or "Artist")
        if album.artist_id:
            self._load_artist_info(album.artist_id)

        self.query_one("#albums-switcher", ContentSwitcher).current = "albums-album-view"
        self._album_view_active = True
        panel.focus_tracklist()

    def _show_grid(self) -> None:
        self.query_one("#albums-switcher", ContentSwitcher).current = "albums-grid-view"
        self._album_view_active = False
        self.query_one("#fav-albums-grid", AlbumGrid).focus()

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

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track)

    @on(events.Click, "#album-breadcrumb")
    def _on_breadcrumb_click(self) -> None:
        self._show_grid()
