from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Label, ListItem, ListView
from textual_image.widget import TGPImage

from ...qobuz.client import QobuzError
from ...qobuz.models import Album, Artist, Track
from .._images import fetch_image
from .._now_playing import (
    GUTTER_NUM,
    NowPlayingRowMixin,
    NowPlayingViewMixin,
    heart_suffix,
    playing_content,
)
from .._utils import (
    DETAIL_HEADER_CSS,
    readout,
    strip_html,
    within,
)
from ..nav import page_header
from ..widgets.art import Art, square_width
from ..widgets.lists import TrackListView
from ..widgets.transport import TransportBar

if TYPE_CHECKING:
    from ..app import QobitApp


# A detail panel's vertical budget (fit_detail_panel: the album panel and the
# playlist page). The tracklist keeps half the panel, at least
# MIN_TRACK_ROWS; the art takes what's left, from ART_MIN to ART_MAX.
ART_MAX = 14
ART_MIN = 5
MIN_TRACK_ROWS = 8
TWO_COL_MIN_W = 90  # an art-less panel this wide splits its tracklist in two
# An artist header sits above the panel only while the panel keeps at least
# this many rows with it: border, a header with 7 rows of art (+ padding),
# the tracklist's rule and MIN_TRACK_ROWS.
_ART_WITH_ARTIST = 7
PANEL_WITH_ARTIST_MIN = 2 + (_ART_WITH_ARTIST + 2) + 1 + MIN_TRACK_ROWS


def fit_detail_panel(panel: Widget, count: int, *, two_col: bool) -> None:
    """Share a detail panel's height (art + info header over a tracklist of
    ``count`` tracks, 0 if unknown) between the two: the tracklist keeps
    half the panel and at least MIN_TRACK_ROWS (fewer for a shorter list,
    whose spare rows go to the art), the art gets the rest between ART_MIN
    and ART_MAX rows. Below ART_MIN the art goes (``-no-art`` on the
    header) and the header keeps the title and the info line; with
    ``two_col`` and the width for it, the tracklist splits in two
    (``-two-col`` on it). The classes sit on the parts, not the panel:
    scoped DEFAULT_CSS can't match a class on the host itself."""
    height = panel.size.height  # inside the border
    if height <= 0:  # hidden (another view is showing)
        return
    want = max(MIN_TRACK_ROWS, height // 2)
    rows = max(3, min(want, count)) if count else want
    budget = height - rows - 1  # the tracklist's top rule
    art = min(ART_MAX, budget - 2)  # the header's padding: a row above, a row below
    no_art = art < ART_MIN
    header = panel.query_one(".detail-header")
    meta = panel.query_one(".detail-meta")
    header.set_class(no_art, "-no-art")
    panel.query_one(".detail-tracklist").set_class(
        two_col and no_art and panel.size.width >= TWO_COL_MIN_W, "-two-col"
    )
    if no_art:
        header.styles.height = meta.styles.height = max(2, min(4, budget))
    else:
        image = panel.query_one(".detail-art", TGPImage)
        header.styles.height = art + 2
        meta.styles.height = image.styles.height = art
        square_width(image, art)


def fit_artist_header(header: Widget, panel: AlbumDetailPanel) -> None:
    """Show ``header`` (above ``panel``) only while the panel keeps
    PANEL_WITH_ARTIST_MIN rows with it. Worked out from the panel's height
    *with* the header, whatever its state now, so toggling it can't make
    the answer flip back."""
    current = panel.outer_size.height
    if current <= 0:
        return
    # Its CSS height: outer_size is 0 while it's hidden.
    shown = header.styles.height.cells if header.styles.height else 0
    with_header = current if header.display else current - (shown or 0)
    show = with_header >= PANEL_WITH_ARTIST_MIN
    if header.display != show:
        header.display = show


class TrackRow(NowPlayingRowMixin, ListItem):
    DEFAULT_CSS = """
    TrackRow { height: 1; padding: 0 1; }
    TrackRow Label { width: 1fr; }
    """

    def __init__(self, track: Track, number: int, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._number = number
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = heart_suffix(self._favorite)
        # Numbered lists show the number only (no ♪), like the Up Next queue;
        # on the playing row ▶/⏸ takes the number's place.
        text = f"{t.display_title}  ·  {t.duration_str}"
        return playing_content(
            f"{self._number}.", text, self._is_playing, self._is_paused, heart, width=GUTTER_NUM
        )

    def compose(self) -> ComposeResult:
        yield Label(self._primary(), classes="row-title")

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self._refresh_primary()


class AlbumDetailPanel(NowPlayingViewMixin, Widget):
    """Reusable inline album detail panel: art, metadata, and track list.

    Usage: call ``load(album)`` to populate. Emits ``TrackSelected`` when the
    user picks a track; the parent decides what to do with it.
    """

    class TrackSelected(Message):
        def __init__(self, track: Track, queue: list[Track]) -> None:
            super().__init__()
            self.track = track
            self.queue = queue

    DEFAULT_CSS = (
        DETAIL_HEADER_CSS
        + """
    AlbumDetailPanel {
        height: 1fr;
        layout: vertical;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-subtitle-color: $qobit-muted;
        border-title-style: bold;
    }
    AlbumDetailPanel:focus-within {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    /* Album-specific extras the shared .detail-* header doesn't cover. */
    AlbumDetailPanel .ap-version { height: auto; color: $qobit-muted; text-style: italic; }
    AlbumDetailPanel .ap-badges { height: auto; color: $qobit-muted; }
    AlbumDetailPanel .ap-awards { height: auto; color: $qobit-accent-text; margin-top: 1; }
    AlbumDetailPanel:focus-within .detail-tracklist {
        border-top: round $accent;
    }
    /* A short, wide panel (fit_detail_panel): the tracklist in two columns. */
    AlbumDetailPanel .detail-tracklist.-two-col {
        layout: grid;
        grid-size: 2;
        grid-rows: 1;
        grid-gutter: 0 2;
    }
    """
    )

    album: Album | None = None
    """The album on show: what the ``i`` menu's album favourite acts on."""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="detail-header"):
            yield Art(classes="detail-art")
            # Not focusable: a scroll container takes focus by default, and
            # Tab or a click on the header left focus there, invisible, with
            # the tracklist one more Tab away. The wheel still scrolls it.
            with VerticalScroll(classes="detail-meta", can_focus=False):
                yield Label("", classes="detail-title", markup=True)
                yield Label("", classes="ap-version", markup=True)
                yield Label("", classes="detail-sub", markup=True)
                yield Label("", classes="ap-badges", markup=True)
                yield Label("", classes="ap-awards", markup=True)
                yield Label("", classes="detail-desc", markup=True)
        yield TrackListView(classes="detail-tracklist")

    class Resized(Message):
        """The panel re-fitted to a new height: hosts with an artist header
        above it re-check whether there's room for it (fit_artist_header)."""

    def on_mount(self) -> None:
        square_width(self.query_one(TGPImage), ART_MAX)
        self._wire_now_playing(TrackRow, ".detail-tracklist")

    def on_click(self, event: events.Click) -> None:
        """A click on the art or the album info selects the tracklist, the
        panel's only focusable part."""
        if within(event.widget, "detail-header"):
            self.focus_tracklist()

    def on_resize(self, _: events.Resize) -> None:
        self._fit()
        self.post_message(self.Resized())

    def _fit(self) -> None:
        count = self.album.tracks_count if self.album and self.album.tracks_count else 0
        fit_detail_panel(self, count, two_col=True)

    def load(self, album: Album) -> None:
        """Populate the panel with album data and start async loading."""
        self.album = album
        self._fit()  # the track count sets the tracklist's share
        self.query_one(Art).placeholder = album.title
        self.border_title = readout("Album")  # the title is in bold right below
        year = str(album.year) if album.year else "—"
        parts: list[str] = [escape(album.artist), year]
        if album.genre:
            parts.append(escape(album.genre))
        self.refresh_heart()
        self._load_heart(album.id)
        # Plain: the CSS gives these lines the muted colour ([dim] on top
        # halved it, 1.5:1 on light themes).
        self.query_one(".detail-sub", Label).update("  ·  ".join(parts))
        for cls in (".ap-version", ".ap-badges", ".ap-awards", ".detail-desc"):
            self._set_optional(cls, "")
        self.query_one(".detail-tracklist", ListView).clear()
        if album.image_url:
            self._fetch_art(album.image_url)
        self._fetch_full_album(album)

    def refresh_heart(self) -> None:
        """The title, with a ♥ when the album is in the user's favourites."""
        if self.album is None:
            return
        app: QobitApp = self.app  # type: ignore[assignment]
        self.query_one(".detail-title", Label).update(
            Content.assemble(self.album.title, heart_suffix(app.is_favorite_album(self.album.id)))
        )

    @work(exclusive=True, group="_load_heart")  # a newer album cancels a stale one
    async def _load_heart(self, album_id: str) -> None:
        """Favourite albums load at startup; if they haven't yet, wait, then
        draw the heart. A failed load just leaves it off."""
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            await app.ensure_favorite_albums()
        except QobuzError:
            return
        if self.is_attached and self.album is not None and self.album.id == album_id:
            self.refresh_heart()

    def focus_tracklist(self) -> None:
        self.query_one(".detail-tracklist", ListView).focus()

    def _set_optional(self, selector: str, content: str) -> None:
        """Update an optional meta label and collapse it (display: none) when
        empty, so it leaves no blank row / margin behind."""
        label = self.query_one(selector, Label)
        label.update(content)
        label.display = bool(content)

    @work(exclusive=True, group="_fetch_art")  # a newer request cancels a stale one
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_attached:
            with contextlib.suppress(NoMatches):
                self.query_one(TGPImage).image = img

    @work(exclusive=True, group="_fetch_full_album")  # a newer request cancels a stale one
    async def _fetch_full_album(self, album: Album) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            full = Album.from_api(await app._client.get_album(album.id))
        except Exception:
            if self.is_attached:
                with contextlib.suppress(NoMatches):
                    self._set_optional(".detail-desc", "Couldn't load album details.")
            return

        if not self.is_attached or self.album is None or self.album.id != full.id:
            return
        self.album = full

        try:
            self._set_optional(
                ".ap-version",
                f"[italic]{escape(full.version)}[/italic]" if full.version else "",
            )
            year = str(full.year) if full.year else "—"
            sub_parts: list[str] = [escape(full.artist), year]
            if full.genre:
                sub_parts.append(escape(full.genre))
            if dur := full.total_duration_str:
                sub_parts.append(dur)
            self.query_one(".detail-sub", Label).update("  ·  ".join(sub_parts))

            badge_parts: list[str] = []
            if full.label:
                badge_parts.append(escape(full.label))
            if q := full.quality_badge:
                # HI-RES in the theme's hi-res colour, like the transport.
                badge_parts.append(q.replace("HI-RES ", "[b $qobit-hires]HI-RES[/] ", 1))
            if full.popularity is not None:
                badge_parts.append(f"★ {full.popularity}")
            self._set_optional(".ap-badges", "  ·  ".join(badge_parts))

            self._set_optional(
                ".ap-awards", "  ".join(escape(a) for a in full.awards) if full.awards else ""
            )
            self._set_optional(
                ".detail-desc", escape(strip_html(full.description)) if full.description else ""
            )
        except NoMatches:
            return

        if not full.tracks:
            return

        fav_ids = await app.ensure_favorite_ids()

        if not self.is_attached:
            return

        try:
            lv = self.query_one(".detail-tracklist", ListView)
            rows = [
                TrackRow(track, i, str(track.id) in fav_ids)
                for i, track in enumerate(full.tracks, 1)
            ]
            self._apply_now_playing(rows)
            await lv.mount(*rows)
            self._place_initial_cursor(lv)
        except NoMatches:
            pass

    @on(ListView.Selected)
    def _on_list_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackRow):
            event.stop()
            lv = self.query_one(".detail-tracklist", ListView)
            rows = list(lv.query(TrackRow))
            idx = rows.index(event.item)
            queue = [r.track for r in rows[idx + 1 :]]
            self.post_message(AlbumDetailPanel.TrackSelected(event.item.track, queue))
            self.call_after_refresh(event.list_view.focus)


class AlbumScreen(Screen):
    """Full-screen album detail pushed from the Search page.

    Wraps AlbumDetailPanel so Search → Album looks identical to the inline
    album view opened from the Albums and Artists tabs: big art, rich metadata
    header, formatted tracklist with favourites. Escape (the app's ``back``)
    closes it."""

    DEFAULT_CSS = """
    AlbumScreen { layout: vertical; }
    /* Bottom margin only: the app's horizontal inset is the Screen's padding
       (see QobitApp.CSS). A left/right margin here put the panel one column
       inside the header and transport bar above and below it. */
    AlbumScreen AlbumDetailPanel { height: 1fr; margin: 0 0 1 0; }
    """

    def __init__(self, album: Album) -> None:
        super().__init__()
        self._album = album

    def nav_levels(self) -> list[str]:
        return [self._album.title]

    def nav_to(self, depth: int) -> None:
        pass  # one level

    def compose(self) -> ComposeResult:
        # Lazy import avoids a circular dependency: artist_detail imports
        # AlbumDetailPanel from this module at the top level.
        from .artist_detail import ArtistHeader

        yield from page_header(self.app)  # type: ignore[arg-type]
        yield ArtistHeader()
        yield AlbumDetailPanel(id="album-panel")
        yield TransportBar()
        yield Footer()

    def on_mount(self) -> None:
        from .artist_detail import ArtistHeader

        self.set_class(getattr(self.app, "_transparent", False), "-transparent")
        panel = self.query_one("#album-panel", AlbumDetailPanel)
        panel.load(self._album)
        header = self.query_one(ArtistHeader)
        header.set_loading(self._album.artist or "Artist")
        # An album built from a track ("Go to album", `g`) has no artist id
        # (Track doesn't carry one), so the header would stay empty. Resolve it
        # from the full album; the client caches it for the panel's own fetch.
        self._load_artist_info(self._album.artist_id)
        self.call_after_refresh(panel.focus_tracklist)

    def on_album_detail_panel_resized(self, _: AlbumDetailPanel.Resized) -> None:
        from .artist_detail import ArtistHeader

        fit_artist_header(self.query_one(ArtistHeader), self.query_one(AlbumDetailPanel))

    @work
    async def _load_artist_info(self, artist_id: str | None) -> None:
        from .artist_detail import ArtistHeader

        app: QobitApp = self.app  # type: ignore[assignment]
        try:
            if not artist_id:
                full = Album.from_api(await app._client.get_album(self._album.id))
                artist_id = full.artist_id
            if not artist_id:
                return
            artist = Artist.from_api(await app._client.get_artist_detail(artist_id))
        except QobuzError:
            return
        if self.is_attached:
            with contextlib.suppress(NoMatches):
                self.query_one(ArtistHeader).populate(artist)

    @on(AlbumDetailPanel.TrackSelected)
    def _on_track_selected(self, event: AlbumDetailPanel.TrackSelected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.play_track(event.track, queue=event.queue)
