from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView
from textual_image.widget import TGPImage

from ...qobuz.models import Playlist, Track
from .._images import fetch_image
from .._now_playing import NowPlayingRowMixin, NowPlayingViewMixin, heart_suffix, playing_content
from .._utils import DETAIL_HEADER_CSS, strip_html
from ..widgets.lists import TrackListView
from ..widgets.transport import TransportBar
from .artist_detail import _set_art_width
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp


class PlaylistTrackRow(NowPlayingRowMixin, ListItem):
    DEFAULT_CSS = """
    PlaylistTrackRow { height: 2; padding: 0 1; }
    PlaylistTrackRow Label { width: 1fr; }
    PlaylistTrackRow .primary { text-style: bold; }
    PlaylistTrackRow .secondary { color: $text-muted; }
    """

    def __init__(self, track: Track, number: int, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._number = number
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = heart_suffix(self._favorite)
        if self._is_playing:
            text = f"{self._number:3}.  {t.artist} — {t.display_title}"
        else:
            text = f"{self._number:3}. {ICON_TRACK}  {t.artist} — {t.display_title}"
        return playing_content(text, self._is_playing, self._is_paused, heart)

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="primary")
        yield Label(f"        {t.album}  ·  {t.duration_str}", classes="secondary")

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self._refresh_primary()


class PlaylistScreen(NowPlayingViewMixin, Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    DEFAULT_CSS = (
        DETAIL_HEADER_CSS
        + """
    PlaylistScreen { layout: vertical; }
    PlaylistScreen #breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $boost;
    }
    PlaylistScreen #breadcrumb:hover {
        color: $text;
        background: $panel;
    }
    PlaylistScreen #playlist-panel {
        height: 1fr;
        margin: 0 1 1 1;
        layout: vertical;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
    }
    PlaylistScreen #playlist-panel:focus-within {
        border: round $accent;
        border-title-color: $accent;
    }
    PlaylistScreen #playlist-panel:focus-within .detail-tracklist {
        border-top: solid $accent;
    }
    """
    )

    def __init__(self, playlist_id: str) -> None:
        super().__init__()
        self._playlist_id = playlist_id

    def compose(self) -> ComposeResult:
        yield Label("← Playlists", id="breadcrumb")
        with Vertical(id="playlist-panel"):
            with Horizontal(classes="detail-header"):
                yield TGPImage(classes="detail-art")
                with VerticalScroll(classes="detail-meta"):
                    yield Label("", classes="detail-title", markup=True)
                    yield Label("", classes="detail-sub", markup=True)
                    yield Label("", classes="detail-desc", markup=True)
            yield TrackListView(classes="detail-tracklist")
        yield TransportBar()
        yield Footer()

    def on_mount(self) -> None:
        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        _set_art_width(self.query_one(".detail-art", TGPImage), 14)
        self.query_one("#playlist-panel").border_title = "Loading…"
        self._wire_now_playing(PlaylistTrackRow, ".detail-tracklist")
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            playlist = Playlist.from_api(await app._client.get_playlist(self._playlist_id))
        except Exception as e:
            if self.is_mounted:
                try:
                    self.query_one("#playlist-panel").border_title = "Error"
                    self.query_one(".detail-title", Label).update(f"[red]{escape(str(e))}[/red]")
                except NoMatches:
                    pass
            return

        if not self.is_mounted:
            return

        try:
            panel = self.query_one("#playlist-panel")
            panel.border_title = escape(playlist.name)

            self.query_one(".detail-title", Label).update(escape(playlist.name))

            sub_parts = [f"by {escape(playlist.owner)}", f"{playlist.tracks_count} tracks"]
            if playlist.duration_str:
                sub_parts.append(playlist.duration_str)
            if playlist.date_str:
                sub_parts.append(playlist.date_str)
            self.query_one(".detail-sub", Label).update(f"[dim]{' · '.join(sub_parts)}[/dim]")

            if playlist.description:
                cleaned = escape(strip_html(playlist.description))
                self.query_one(".detail-desc", Label).update(f"[dim]{cleaned}[/dim]")
        except NoMatches:
            return

        if playlist.image_url:
            self._fetch_art(playlist.image_url)

        if not playlist.tracks:
            return

        fav_ids = await app.ensure_favorite_ids()
        if not self.is_mounted:
            return

        try:
            lv = self.query_one(".detail-tracklist", ListView)
            rows = [
                PlaylistTrackRow(t, i, str(t.id) in fav_ids)
                for i, t in enumerate(playlist.tracks, 1)
            ]
            self._apply_now_playing(rows)
            await lv.mount(*rows)
        except NoMatches:
            return

        if self.is_mounted:
            try:
                self.call_after_refresh(self.query_one(".detail-tracklist", ListView).focus)
            except NoMatches:
                pass

    @work
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_mounted:
            try:
                self.query_one(".detail-art", TGPImage).image = img
            except NoMatches:
                pass

    @on(ListView.Selected, ".detail-tracklist")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, PlaylistTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            lv = self.query_one(".detail-tracklist", ListView)
            rows = list(lv.query(PlaylistTrackRow))
            idx = rows.index(event.item)
            queue = [r.track for r in rows[idx + 1 :]]
            app.play_track(event.item.track, queue=queue)
            self.call_after_refresh(lv.focus)

    @on(events.Click, "#breadcrumb")
    def _on_breadcrumb_click(self) -> None:
        self.app.pop_screen()
