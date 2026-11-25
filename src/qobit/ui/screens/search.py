from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView

from ...qobuz.client import QobuzError
from ...qobuz.models import Album, Artist, Playlist, Track
from .._now_playing import (
    GUTTER_ICON,
    NowPlayingRowMixin,
    NowPlayingViewMixin,
    gutter,
    heart_suffix,
    meta_indent,
    playing_content,
)
from .._utils import qapp, readout, status_line
from ..widgets.lists import TrackListView

if TYPE_CHECKING:
    pass

ICON_TRACK = "♪"
ICON_ARTIST = "⊙"
ICON_ALBUM = "◎"
ICON_PLAYLIST = "≡"


# ── result item widgets (shared with library tabs) ────────────────────────────


class TrackItem(NowPlayingRowMixin, ListItem):
    DEFAULT_CSS = """
    TrackItem { height: 2; padding: 0 1; }
    TrackItem Label { width: 1fr; }
    TrackItem .row-title { text-style: bold; }
    TrackItem .row-meta { color: $qobit-muted; }
    """

    def __init__(self, track: Track, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = heart_suffix(self._favorite)
        text = f"{t.artist} — {t.display_title}"
        return playing_content(ICON_TRACK, text, self._is_playing, self._is_paused, heart)

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="row-title")
        yield Label(
            f"{meta_indent(GUTTER_ICON)}{t.album}  ·  {t.duration_str}",
            classes="row-meta",
            markup=False,
        )

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self._refresh_primary()


class AlbumItem(ListItem):
    DEFAULT_CSS = """
    AlbumItem { height: 2; padding: 0 1; }
    AlbumItem Label { width: 1fr; }
    AlbumItem .row-title { text-style: bold; }
    AlbumItem .row-meta { color: $qobit-muted; }
    """

    def __init__(self, album: Album) -> None:
        super().__init__()
        self.album = album

    def compose(self) -> ComposeResult:
        a = self.album
        year = str(a.year) if a.year else "—"
        yield Label(gutter(ICON_ALBUM, GUTTER_ICON) + a.title, classes="row-title", markup=False)
        yield Label(
            f"{meta_indent(GUTTER_ICON)}{a.artist}  ·  {year}  ·  {a.tracks_count} tracks",
            classes="row-meta",
            markup=False,
        )


class ArtistItem(ListItem):
    DEFAULT_CSS = """
    ArtistItem { height: 2; padding: 0 1; }
    ArtistItem Label { width: 1fr; }
    ArtistItem .row-title { text-style: bold; }
    ArtistItem .row-meta { color: $qobit-muted; }
    """

    def __init__(self, artist: Artist) -> None:
        super().__init__()
        self.artist = artist

    def compose(self) -> ComposeResult:
        a = self.artist
        n = a.albums_count
        sub = f"{n} album{'' if n == 1 else 's'}" if n else ""
        yield Label(gutter(ICON_ARTIST, GUTTER_ICON) + a.name, classes="row-title", markup=False)
        yield Label(f"{meta_indent(GUTTER_ICON)}{sub}", classes="row-meta", markup=False)


class PlaylistItem(ListItem):
    DEFAULT_CSS = """
    PlaylistItem { height: 2; padding: 0 1; }
    PlaylistItem Label { width: 1fr; }
    PlaylistItem .row-title { text-style: bold; }
    PlaylistItem .row-meta { color: $qobit-muted; }
    """

    def __init__(self, playlist: Playlist) -> None:
        super().__init__()
        self.playlist = playlist

    def compose(self) -> ComposeResult:
        p = self.playlist
        yield Label(gutter(ICON_PLAYLIST, GUTTER_ICON) + p.name, classes="row-title", markup=False)
        yield Label(
            f"{meta_indent(GUTTER_ICON)}{p.owner}  ·  {p.tracks_count} tracks",
            classes="row-meta",
            markup=False,
        )


# ── search view ───────────────────────────────────────────────────────────────


class SearchView(NowPlayingViewMixin, Widget):
    BINDINGS = [Binding("/", "focus_input", "Search")]

    _search_version: int = 0

    DEFAULT_CSS = """
    SearchView {
        height: 1fr;
        layout: vertical;
    }
    SearchView Input {
        margin: 0;
        border: round $accent 40%;
        border-subtitle-align: right;
        border-subtitle-color: $qobit-muted;
    }
    SearchView Input:focus {
        border: round $accent;
    }
    #artists-results, #tracks-results, #albums-results {
        height: 1fr;
        margin: 0;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-subtitle-color: $qobit-muted;
        border-title-style: bold;
    }
    #artists-results:focus, #tracks-results:focus, #albums-results:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    """

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search Qobuz…", id="search-input")
        yield ListView(id="artists-results")
        yield TrackListView(id="tracks-results")
        yield ListView(id="albums-results")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).border_subtitle = "⏎"
        self.query_one("#artists-results", ListView).border_title = readout("Artists")
        self.query_one("#tracks-results", ListView).border_title = readout("Tracks")
        self.query_one("#albums-results", ListView).border_title = readout("Albums")
        self.query_one("#search-input").focus()
        self._wire_now_playing(TrackItem, "#tracks-results")

    def action_focus_input(self) -> None:
        self.query_one("#search-input").focus()

    @on(Input.Submitted, "#search-input")
    def _on_submit(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._search_version += 1
            self._search(query, self._search_version)

    @work
    async def _search(self, query: str, version: int) -> None:
        app = qapp(self)
        lv_artists = self.query_one("#artists-results", ListView)
        lv_tracks = self.query_one("#tracks-results", ListView)
        lv_albums = self.query_one("#albums-results", ListView)

        try:
            tracks_r, albums_r, artists_r, fav_ids = await asyncio.gather(
                app._client.search(query, type="tracks", limit=5),
                app._client.search(query, type="albums", limit=5),
                app._client.search(query, type="artists", limit=5),
                app.library.ensure_track_ids(),
            )
        except QobuzError as e:
            if version != self._search_version:
                return
            msg = str(e)
            await asyncio.gather(lv_artists.clear(), lv_tracks.clear(), lv_albums.clear())
            await lv_artists.append(ListItem(status_line(msg, error=True)))
            lv_artists.focus()
            return

        if version != self._search_version:
            return

        tracks = tracks_r.get("tracks", {}).get("items", [])
        albums = albums_r.get("albums", {}).get("items", [])
        artists = artists_r.get("artists", {}).get("items", [])

        await asyncio.gather(lv_artists.clear(), lv_tracks.clear(), lv_albums.clear())

        for raw in artists:
            await lv_artists.append(ArtistItem(Artist.from_api(raw)))
        track_rows = [
            TrackItem(t, str(t.id) in fav_ids) for t in (Track.from_api(raw) for raw in tracks)
        ]
        self._apply_now_playing(track_rows)
        await lv_tracks.mount(*track_rows)
        for raw in albums:
            await lv_albums.append(AlbumItem(Album.from_api(raw)))

        first_non_empty = next(
            (lv for lv in (lv_artists, lv_tracks, lv_albums) if len(lv) > 0), None
        )
        if first_non_empty is not None:
            first_non_empty.focus()
        else:
            await lv_artists.append(ListItem(status_line("No results.")))
            lv_artists.focus()

    @on(ListView.Selected)
    def _on_selected(self, event: ListView.Selected) -> None:
        from .album_detail import AlbumScreen
        from .artist_detail import ArtistScreen
        from .playlist_detail import PlaylistScreen

        app = qapp(self)
        item = event.item
        if isinstance(item, TrackItem):
            lv = self.query_one("#tracks-results", ListView)
            rows = list(lv.query(TrackItem))
            idx = next((i for i, r in enumerate(rows) if r is item), -1)
            queue = [r.track for r in rows[idx + 1 :]] if idx >= 0 else []
            app.play_track(item.track, queue=queue)
            event.list_view.focus()
        elif isinstance(item, AlbumItem):
            app.push_screen(AlbumScreen(item.album))
        elif isinstance(item, ArtistItem):
            app.push_screen(ArtistScreen(item.artist.id))
        elif isinstance(item, PlaylistItem):
            app.push_screen(PlaylistScreen(item.playlist.id))
