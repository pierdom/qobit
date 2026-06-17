from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView

from ...qobuz.models import Artist, Track
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp


class ArtistTrackRow(ListItem):
    DEFAULT_CSS = """
    ArtistTrackRow { height: 2; padding: 0 1; }
    ArtistTrackRow Label { width: 1fr; }
    """

    def __init__(self, track: Track, number: int) -> None:
        super().__init__()
        self.track = track
        self._number = number

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(
            f"[dim]{self._number}. {ICON_TRACK}[/dim]  {escape(t.display_title)}",
            markup=True,
        )
        yield Label(f"     [dim]{escape(t.album)}  ·  {t.duration_str}[/dim]", markup=True)


class ArtistScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    DEFAULT_CSS = """
    ArtistScreen { layout: vertical; }

    ArtistScreen #breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $boost;
    }

    ArtistScreen #artist-header {
        height: auto;
        padding: 1 2 1 2;
        background: $boost;
    }

    ArtistScreen #artist-name {
        text-style: bold;
        height: auto;
    }

    ArtistScreen #bio {
        height: auto;
        max-height: 5;
        margin-top: 1;
        color: $text-muted;
    }

    ArtistScreen #tracks-label {
        height: 1;
        padding: 0 2;
        background: $boost;
        color: $text-muted;
        text-style: bold;
    }

    ArtistScreen ListView { height: 1fr; }
    """

    def __init__(self, artist_id: str, source: str = "Search") -> None:
        super().__init__()
        self._artist_id = artist_id
        self._source = source

    def compose(self) -> ComposeResult:
        yield Label(f"← {self._source}", id="breadcrumb")
        with Vertical(id="artist-header"):
            yield Label("Loading…", id="artist-name")
            yield Label("", id="bio")
        yield Label("TOP TRACKS", id="tracks-label")
        yield ListView(id="top-tracks")
        yield Footer()

    def on_mount(self) -> None:
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        artist = Artist.from_api(await app._client.get_artist_page(self._artist_id))

        self.query_one("#artist-name", Label).update(f"[bold]{escape(artist.name)}[/bold]")

        if artist.biography:
            bio = artist.biography
            if len(bio) > 400:
                bio = bio[:400].rsplit(" ", 1)[0] + "…"
            self.query_one("#bio", Label).update(escape(bio))

        lv = self.query_one("#top-tracks", ListView)
        for i, track in enumerate(artist.tracks, 1):
            await lv.append(ArtistTrackRow(track, i))

    @on(ListView.Selected, "#top-tracks")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ArtistTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.play_track(event.item.track)
