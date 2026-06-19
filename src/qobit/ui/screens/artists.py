from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Label, ListView

from ...qobuz.models import Album, Artist, Track
from .album_detail import AlbumDetailPanel
from .artist_detail import (
    AlbumCard,
    AlbumGrid,
    ArtistCard,
    ArtistGrid,
    ArtistHeader,
    ArtistTrackRow,
)

if TYPE_CHECKING:
    from ..app import QobitApp

_SORT_OPTIONS: list[tuple[str, str]] = [
    ("favorited_at", "Date Added"),
    ("name", "Name"),
]
_SORT_KEYS = [k for k, _ in _SORT_OPTIONS]


class ArtistsView(Widget):
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
        border-title-color: $accent 40%;
        border-title-style: bold;
        border-subtitle-color: $accent 40%;
        border-subtitle-align: right;
    }
    ArtistsView ArtistGrid:focus {
        border: round $accent;
        border-title-color: $accent;
        border-subtitle-color: $accent;
    }
    ArtistsView #artist-breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $boost;
    }
    ArtistsView #artist-breadcrumb:hover {
        color: $text;
        background: $panel;
    }
    ArtistsView #artist-detail-view { height: 1fr; }
    ArtistsView #artist-content-switcher { height: 1fr; }
    ArtistsView #top-tracks {
        height: 1fr;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }
    ArtistsView #top-tracks:focus {
        border: round $accent;
        border-title-color: $accent;
    }
    ArtistsView #artist-albums-grid {
        height: 1fr;
        grid-rows: 4;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }
    ArtistsView #artist-albums-grid:focus {
        border: round $accent;
        border-title-color: $accent;
    }
    ArtistsView #album-breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $boost;
    }
    ArtistsView #album-breadcrumb:hover {
        color: $text;
        background: $panel;
    }
    """

    _view: str = "grid"  # "grid" | "artist" | "album"
    _loaded: bool = False
    _sort_key: str = "favorited_at"
    _sort_reverse: bool = True
    _artists: list[Artist]
    _current_artist: Artist | None

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._artists = []
        self._current_artist = None

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="artists-grid-view", id="artists-switcher"):
            with Vertical(id="artists-grid-view"):
                yield ArtistGrid(id="fav-artists-grid", tile_min_width=33)
            with Vertical(id="artist-detail-view"):
                yield Label("← Favourite Artists", id="artist-breadcrumb")
                yield ArtistHeader()
                with ContentSwitcher(
                    initial="artist-tracks-albums-view", id="artist-content-switcher"
                ):
                    with Vertical(id="artist-tracks-albums-view"):
                        yield ListView(id="top-tracks")
                        yield AlbumGrid(id="artist-albums-grid")
                    with Vertical(id="artist-album-detail-view"):
                        yield Label("", id="album-breadcrumb")
                        yield AlbumDetailPanel(id="album-panel")

    def on_mount(self) -> None:
        self.query_one("#fav-artists-grid", ArtistGrid).border_title = "Favourite Artists"
        self._update_subtitle()
        self.query_one("#top-tracks", ListView).border_title = "Top Tracks"
        self.query_one("#artist-albums-grid", AlbumGrid).border_title = "Albums & EPs"

    def on_show(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._load()
        if self._view == "album":
            self.call_after_refresh(
                self.query_one("#album-panel", AlbumDetailPanel).focus_tracklist
            )
        elif self._view == "artist":
            self.call_after_refresh(self.query_one("#artist-albums-grid", AlbumGrid).focus)
        else:
            self.call_after_refresh(self.query_one("#fav-artists-grid", ArtistGrid).focus)

    def action_navigate_back(self) -> bool:
        if self._view == "album":
            self._show_artist_content()
            return True
        if self._view == "artist":
            self._show_grid()
            return True
        return False

    def action_cycle_sort(self) -> None:
        if self._view != "grid":
            return
        idx = (_SORT_KEYS.index(self._sort_key) + 1) % len(_SORT_KEYS)
        self._sort_key = _SORT_KEYS[idx]
        self._sort_reverse = self._sort_key == "favorited_at"
        self._apply_sort()

    def action_toggle_reverse(self) -> None:
        if self._view != "grid":
            return
        self._sort_reverse = not self._sort_reverse
        self._apply_sort()

    def _update_subtitle(self) -> None:
        arrow = "↓" if self._sort_reverse else "↑"
        label = dict(_SORT_OPTIONS)[self._sort_key]
        self.query_one("#fav-artists-grid", ArtistGrid).border_subtitle = f"{arrow} {label}"

    def _apply_sort(self) -> None:
        self._update_subtitle()
        self._render_grid()

    def _sorted_artists(self) -> list[Artist]:
        def key(a: Artist) -> object:
            if self._sort_key == "favorited_at":
                return a.favorited_at or 0
            if self._sort_key == "name":
                return a.name.lower()
            return 0

        return sorted(self._artists, key=key, reverse=self._sort_reverse)

    def _render_grid(self) -> None:
        grid = self.query_one("#fav-artists-grid", ArtistGrid)
        grid._cursor = -1
        self._mount_cards(self._sorted_artists())

    @work
    async def _mount_cards(self, artists: list[Artist]) -> None:
        grid = self.query_one("#fav-artists-grid", ArtistGrid)
        await grid.remove_children()
        if not artists:
            await grid.mount(Label("[dim]No favourite artists yet.[/dim]", markup=True))
            return
        await grid.mount(*[ArtistCard(artist) for artist in artists])

    @work
    async def _open_artist(self, artist: Artist) -> None:
        self._current_artist = artist
        self.query_one(ArtistHeader).set_loading(artist.name)

        lv = self.query_one("#top-tracks", ListView)
        grid = self.query_one("#artist-albums-grid", AlbumGrid)
        await lv.clear()
        grid._cursor = -1
        await grid.remove_children()

        inner_cs = self.query_one("#artist-content-switcher", ContentSwitcher)
        inner_cs.current = "artist-tracks-albums-view"
        self.query_one("#artists-switcher", ContentSwitcher).current = "artist-detail-view"
        self._view = "artist"
        self.call_after_refresh(grid.focus)

        self._load_artist_detail(artist.id)
        self._load_artist_tracks(artist.id)

    def _open_album(self, album: Album) -> None:
        panel = self.query_one("#album-panel", AlbumDetailPanel)
        panel.load(album)
        artist_name = self._current_artist.name if self._current_artist else "Artist"
        self.query_one("#album-breadcrumb", Label).update(f"← {artist_name}")
        inner_cs = self.query_one("#artist-content-switcher", ContentSwitcher)
        inner_cs.current = "artist-album-detail-view"
        self._view = "album"
        panel.focus_tracklist()

    def _show_artist_content(self) -> None:
        inner_cs = self.query_one("#artist-content-switcher", ContentSwitcher)
        inner_cs.current = "artist-tracks-albums-view"
        self._view = "artist"
        self.query_one("#artist-albums-grid", AlbumGrid).focus()

    def _show_grid(self) -> None:
        self.query_one("#artists-switcher", ContentSwitcher).current = "artists-grid-view"
        self._view = "grid"
        self.query_one("#fav-artists-grid", ArtistGrid).focus()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        grid = self.query_one("#fav-artists-grid", ArtistGrid)
        try:
            items = await app._client.get_all_favorite_artists()
        except Exception as e:
            await grid.mount(Label(f"[red]{e}[/red]", markup=True))
            return
        self._artists = [Artist.from_api(raw) for raw in items]
        self._render_grid()

    @work
    async def _load_artist_detail(self, artist_id: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        artist = Artist.from_api(await app._client.get_artist_detail(artist_id))
        self.query_one(ArtistHeader).populate(artist)
        if artist.albums:
            grid = self.query_one("#artist-albums-grid", AlbumGrid)
            await grid.mount(*[AlbumCard(album) for album in artist.albums])

    @work
    async def _load_artist_tracks(self, artist_id: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        items = await app._client.get_artist_top_tracks(artist_id)
        if items:
            lv = self.query_one("#top-tracks", ListView)
            rows = [ArtistTrackRow(Track.from_api(raw), i) for i, raw in enumerate(items, 1)]
            await lv.mount(*rows)

    @on(ArtistCard.Selected)
    def _on_artist_selected(self, event: ArtistCard.Selected) -> None:
        self._open_artist(event.artist)

    @on(AlbumCard.Selected)
    def _on_album_selected(self, event: AlbumCard.Selected) -> None:
        self._open_album(event.album)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track)

    @on(ListView.Selected, "#top-tracks")
    def _on_top_track_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ArtistTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.play_track(event.item.track)

    @on(events.Click, "#artist-breadcrumb")
    def _on_artist_breadcrumb_click(self) -> None:
        self._show_grid()

    @on(events.Click, "#album-breadcrumb")
    def _on_album_breadcrumb_click(self) -> None:
        self._show_artist_content()
