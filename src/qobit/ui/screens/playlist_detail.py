from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView
from textual_image.widget import TGPImage

from ...qobuz.models import Playlist, Track
from .._images import fetch_image
from .._now_playing import (
    GUTTER_NUM_LONG,
    NowPlayingRowMixin,
    NowPlayingViewMixin,
    heart_suffix,
    meta_indent,
    playing_content,
)
from .._utils import DETAIL_HEADER_CSS, readout, strip_html
from ..nav import page_header
from ..widgets.art import Art
from ..widgets.lists import TrackListView
from ..widgets.transport import TransportBar
from .artist_detail import _set_art_width

if TYPE_CHECKING:
    from ..app import QobitApp


class PlaylistTrackRow(NowPlayingRowMixin, ListItem):
    DEFAULT_CSS = """
    PlaylistTrackRow { height: 2; padding: 0 1; }
    PlaylistTrackRow Label { width: 1fr; }
    PlaylistTrackRow .row-title { text-style: bold; }
    PlaylistTrackRow .row-meta { color: $text-muted; }
    """

    def __init__(self, track: Track, number: int, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._number = number
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = heart_suffix(self._favorite)
        # Numbered list: the number only, no ♪ (like the album tracklist).
        text = f"{t.artist} — {t.display_title}"
        return playing_content(
            f"{self._number}.",
            text,
            self._is_playing,
            self._is_paused,
            heart,
            width=GUTTER_NUM_LONG,
        )

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="row-title")
        yield Label(
            f"{meta_indent(GUTTER_NUM_LONG)}{t.album}  ·  {t.duration_str}",
            classes="row-meta",
            markup=False,
        )

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self._refresh_primary()


class PlaylistScreen(NowPlayingViewMixin, Screen):
    """A playlist page pushed from the Playlists tab or Search."""

    DEFAULT_CSS = (
        DETAIL_HEADER_CSS
        + """
    PlaylistScreen { layout: vertical; }
    PlaylistScreen #playlist-panel {
        height: 1fr;
        /* Bottom only: the app's inset is the Screen's padding; side margins
           put the panel a column inside the transport bar below it. */
        margin: 0 0 1 0;
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
        border-top: round $accent;
    }
    """
    )

    def __init__(self, playlist_id: str) -> None:
        super().__init__()
        self._playlist_id = playlist_id
        self._name = "Playlist"

    def nav_levels(self) -> list[str]:
        return [self._name]

    def nav_to(self, depth: int) -> None:
        pass  # one level

    def compose(self) -> ComposeResult:
        yield from page_header(self.app)  # type: ignore[arg-type]
        with Vertical(id="playlist-panel"):
            with Horizontal(classes="detail-header"):
                yield Art(classes="detail-art")
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
        self.query_one("#playlist-panel").border_title = readout("Playlist")
        self.query_one("#playlist-panel").border_subtitle = "loading…"
        self._wire_now_playing(PlaylistTrackRow, ".detail-tracklist")
        self._load()

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            playlist = Playlist.from_api(await app._client.get_playlist(self._playlist_id))
        except Exception as e:
            if self.is_attached:
                try:
                    self.query_one("#playlist-panel").border_title = readout("Playlist")
                    self.query_one(".detail-title", Label).update(f"[$error]{escape(str(e))}[/]")
                except NoMatches:
                    pass
            return

        if not self.is_attached:
            return

        try:
            panel = self.query_one("#playlist-panel")
            self._name = playlist.name
            self.query_one(Art).placeholder = playlist.name
            app.nav_changed()
            panel.border_subtitle = ""
            panel.border_title = readout("Playlist")  # the name is in bold right below

            self.query_one(".detail-title", Label).update(escape(playlist.name))

            sub_parts = [f"by {escape(playlist.owner)}", f"{playlist.tracks_count} tracks"]
            if playlist.duration_str:
                sub_parts.append(playlist.duration_str)
            if playlist.date_str:
                sub_parts.append(playlist.date_str)
            self.query_one(".detail-sub", Label).update(f"[dim]{'  ·  '.join(sub_parts)}[/dim]")

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
        if not self.is_attached:
            return

        try:
            lv = self.query_one(".detail-tracklist", ListView)
            rows = [
                PlaylistTrackRow(t, i, str(t.id) in fav_ids)
                for i, t in enumerate(playlist.tracks, 1)
            ]
            self._apply_now_playing(rows)
            await lv.set_rows(rows)  # type: ignore[attr-defined]  # lazy: playlists can be long
        except NoMatches:
            return

        if self.is_attached:
            with contextlib.suppress(NoMatches):
                self.call_after_refresh(self.query_one(".detail-tracklist", ListView).focus)

    @work
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_attached:
            with contextlib.suppress(NoMatches):
                self.query_one(".detail-art", TGPImage).image = img

    @on(ListView.Selected, ".detail-tracklist")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, PlaylistTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            lv = self.query_one(".detail-tracklist", ListView)
            rows = [r for r in lv.rows if isinstance(r, PlaylistTrackRow)]  # type: ignore[attr-defined]
            idx = rows.index(event.item)
            queue = [r.track for r in rows[idx + 1 :]]
            app.play_track(event.item.track, queue=queue)
            self.call_after_refresh(lv.focus)
