from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView

from ...qobuz.client import QobuzError
from ...qobuz.models import Album, Artist, Playlist, Track

if TYPE_CHECKING:
    from ..app import QobitApp

ICON_TRACK = "♪"
ICON_ARTIST = "⊙"
ICON_ALBUM = "◎"
ICON_PLAYLIST = "≡"


# ── result item widgets (shared with library tabs) ────────────────────────────


class SectionHeader(ListItem):
    DEFAULT_CSS = """
    SectionHeader {
        height: 1;
        padding: 0 1;
        background: $boost;
    }
    SectionHeader:hover { background: $boost; }
    SectionHeader > Label { color: $text-muted; text-style: bold; }
    """

    def __init__(self, title: str) -> None:
        super().__init__(disabled=True)
        self._title = title

    def compose(self) -> ComposeResult:
        yield Label(self._title.upper())


class TrackItem(ListItem):
    DEFAULT_CSS = """
    TrackItem { height: 2; padding: 0 1; }
    TrackItem Label { width: 1fr; }
    TrackItem .primary { text-style: bold; }
    TrackItem .secondary { color: $text-muted; }
    """

    def __init__(self, track: Track) -> None:
        super().__init__()
        self.track = track

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(f"{ICON_TRACK}  {t.artist} — {t.display_title}", classes="primary")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")


class AlbumItem(ListItem):
    DEFAULT_CSS = """
    AlbumItem { height: 2; padding: 0 1; }
    AlbumItem Label { width: 1fr; }
    AlbumItem .primary { text-style: bold; }
    AlbumItem .secondary { color: $text-muted; }
    """

    def __init__(self, album: Album) -> None:
        super().__init__()
        self.album = album

    def compose(self) -> ComposeResult:
        a = self.album
        year = str(a.year) if a.year else "—"
        yield Label(f"{ICON_ALBUM}  {a.title}", classes="primary")
        yield Label(f"     {a.artist}  ·  {year}  ·  {a.tracks_count} tracks", classes="secondary")


class ArtistItem(ListItem):
    DEFAULT_CSS = """
    ArtistItem { height: 2; padding: 0 1; }
    ArtistItem Label { width: 1fr; }
    ArtistItem .primary { text-style: bold; }
    ArtistItem .secondary { color: $text-muted; }
    """

    def __init__(self, artist: Artist) -> None:
        super().__init__()
        self.artist = artist

    def compose(self) -> ComposeResult:
        a = self.artist
        sub = f"{a.albums_count} albums" if a.albums_count else ""
        yield Label(f"{ICON_ARTIST}  {a.name}", classes="primary")
        yield Label(f"     {sub}", classes="secondary")


class PlaylistItem(ListItem):
    DEFAULT_CSS = """
    PlaylistItem { height: 2; padding: 0 1; }
    PlaylistItem Label { width: 1fr; }
    PlaylistItem .primary { text-style: bold; }
    PlaylistItem .secondary { color: $text-muted; }
    """

    def __init__(self, playlist: Playlist) -> None:
        super().__init__()
        self.playlist = playlist

    def compose(self) -> ComposeResult:
        p = self.playlist
        yield Label(f"{ICON_PLAYLIST}  {p.name}", classes="primary")
        yield Label(f"     {p.owner}  ·  {p.tracks_count} tracks", classes="secondary")


# ── search view ───────────────────────────────────────────────────────────────


class SearchView(Widget):
    BINDINGS = [Binding("/", "focus_input", show=False)]

    DEFAULT_CSS = """
    SearchView {
        height: 1fr;
        layout: vertical;
    }
    SearchView Input {
        margin: 1 1 0 1;
    }
    SearchView ListView {
        height: 1fr;
        margin: 0 1 1 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search Qobuz…", id="search-input")
        yield ListView(id="results")

    def on_mount(self) -> None:
        self.query_one("#search-input").focus()

    def action_focus_input(self) -> None:
        self.query_one("#search-input").focus()

    @on(Input.Submitted, "#search-input")
    def _on_submit(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._search(query)

    @work
    async def _search(self, query: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#results", ListView)
        await lv.clear()

        try:
            tracks_r, albums_r, artists_r = await asyncio.gather(
                app._client.search(query, type="tracks", limit=5),
                app._client.search(query, type="albums", limit=5),
                app._client.search(query, type="artists", limit=5),
            )
        except (QobuzError, AssertionError) as e:
            msg = str(e) if isinstance(e, QobuzError) else "Not authenticated — run: qobit auth"
            await lv.append(ListItem(Label(f"[red]{msg}[/red]", markup=True)))
            return

        tracks = tracks_r.get("tracks", {}).get("items", [])
        albums = albums_r.get("albums", {}).get("items", [])
        artists = artists_r.get("artists", {}).get("items", [])

        if not tracks and not albums and not artists:
            await lv.append(ListItem(Label("[dim]No results.[/dim]", markup=True)))
            return

        if tracks:
            await lv.append(SectionHeader("Tracks"))
            for raw in tracks:
                await lv.append(TrackItem(Track.from_api(raw)))
        if artists:
            await lv.append(SectionHeader("Artists"))
            for raw in artists:
                await lv.append(ArtistItem(Artist.from_api(raw)))
        if albums:
            await lv.append(SectionHeader("Albums"))
            for raw in albums:
                await lv.append(AlbumItem(Album.from_api(raw)))

    @on(ListView.Selected, "#results")
    def _on_selected(self, event: ListView.Selected) -> None:
        from .album_detail import AlbumScreen
        from .artist_detail import ArtistScreen

        app: QobitApp = self.app  # type: ignore[assignment]
        item = event.item
        if isinstance(item, TrackItem):
            app.play_track(item.track)
        elif isinstance(item, AlbumItem):
            app.push_screen(AlbumScreen(item.album.id))
        elif isinstance(item, ArtistItem):
            app.push_screen(ArtistScreen(item.artist.id, source="Search"))
