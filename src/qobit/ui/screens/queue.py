from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.events import Resize
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from ...qobuz.models import Track
from .._images import fetch_image
from .._utils import strip_html
from ..widgets.lists import TrackListView
from .search import ICON_FAV

if TYPE_CHECKING:
    from ..app import QobitApp


class SectionHeader(ListItem):
    """A non-selectable, full-width divider labelling a queue section.

    The rule spans the list width (recomputed on resize) with the title on the
    left and an optional count on the right: ``── Up Next ──────── 35 ──``."""

    DEFAULT_CSS = """
    SectionHeader { height: 1; padding: 0 1; background: $surface; }
    SectionHeader.-spaced { margin-top: 1; }
    SectionHeader Label { width: 1fr; color: $accent; text-style: bold; }
    """

    def __init__(self, title: str, count: int | None = None, spaced: bool = False) -> None:
        super().__init__(disabled=True)
        self._title = title
        self._count = count
        if spaced:
            self.add_class("-spaced")

    def compose(self) -> ComposeResult:
        yield Label(self._line(56))

    def on_resize(self, event: Resize) -> None:
        # event.size is the border-box width; subtract our own horizontal padding
        # to get the Label's actual content width.
        pad = self.styles.padding.left + self.styles.padding.right
        self.query_one(Label).update(self._line(event.size.width - pad))

    def _line(self, width: int) -> Text:
        left = f"── {self._title} "
        right = f" {self._count} ──" if self._count is not None else "──"
        # `width` is the Label's content width; -1 leaves slack for the list's
        # scrollbar gutter so the rule never wraps onto a second line.
        fill = max(2, width - len(left) - len(right) - 1)
        return Text(left + "─" * fill + right, no_wrap=True, overflow="crop")


class QueueTrackRow(ListItem):
    DEFAULT_CSS = """
    QueueTrackRow { height: 2; padding: 0 1; }
    QueueTrackRow Label { width: 1fr; }
    QueueTrackRow .primary { text-style: bold; }
    QueueTrackRow .secondary { color: $text-muted; }
    """

    def __init__(self, track: Track, number: int, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._number = number
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = (f"  {ICON_FAV}", "$accent") if self._favorite else ""
        return Content.assemble(f"{self._number:>2}.  {t.artist} — {t.display_title}", heart)

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="primary")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self.query_one(".primary", Label).update(self._primary())


class HistoryTrackRow(ListItem):
    """A previously-played track. Muted; selecting it replays the track without
    disturbing the Up Next queue."""

    DEFAULT_CSS = """
    HistoryTrackRow { height: 2; padding: 0 1; }
    HistoryTrackRow Label { width: 1fr; }
    HistoryTrackRow .primary { color: $text-muted; }
    HistoryTrackRow .secondary { color: $text-muted 60%; }
    """

    def __init__(self, track: Track, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = (f"  {ICON_FAV}", "$accent") if self._favorite else ""
        return Content.assemble(f"↺  {t.artist} — {t.display_title}", heart)

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="primary")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self.query_one(".primary", Label).update(self._primary())


class NowPlayingListRow(ListItem):
    """The currently-playing track shown in the left timeline, at the tail of the
    Recently Played region — the 'now' point flowing from history into Up Next.
    Accent-styled with a live ▶/⏸ icon; favourite-able via the list's ``f``."""

    DEFAULT_CSS = """
    NowPlayingListRow { height: 2; padding: 0 1; background: $accent 8%; }
    NowPlayingListRow Label { width: 1fr; }
    NowPlayingListRow .primary { text-style: bold; color: $accent; }
    NowPlayingListRow .secondary { color: $accent 60%; }
    """

    def __init__(self, track: Track, is_paused: bool = False, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._is_paused = is_paused
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        icon = "⏸" if self._is_paused else "▶"
        heart = (f"  {ICON_FAV}", "$accent") if self._favorite else ""
        return Content.assemble(f"{icon}  {t.artist} — {t.display_title}", heart)

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="primary")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")

    def set_paused(self, paused: bool) -> None:
        self._is_paused = paused
        self.query_one(".primary", Label).update(self._primary())

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self.query_one(".primary", Label).update(self._primary())


class NowPlayingHero(Widget):
    """Large now-playing focal panel: album art, rich metadata, artist bio.

    The Queue page's right pane. Self-wires to QobitApp reactives on mount (the
    same pattern as TransportBar) so it stays live wherever it's mounted. ``f``
    toggles the now-playing track's favourite state when the panel is focused.
    No progress bar — the always-on transport bar at the bottom owns playback
    position/seek; the hero fills its space with the artist biography instead."""

    can_focus = True
    BINDINGS = [
        Binding("f", "toggle_favorite", "Favourite", show=False),
        Binding("up", "scroll_up", "Scroll up", show=False),
        Binding("down", "scroll_down", "Scroll down", show=False),
        Binding("pageup", "scroll_page_up", "Page up", show=False),
        Binding("pagedown", "scroll_page_down", "Page down", show=False),
    ]

    _favorite: bool = False

    DEFAULT_CSS = """
    NowPlayingHero {
        width: 2fr;
        max-width: 66;
        height: 1fr;
        border: round $accent 40%;
        layout: vertical;
        align-horizontal: center;
        padding: 1 2;
    }
    NowPlayingHero:focus {
        border: round $accent;
    }
    NowPlayingHero #hero-art-row {
        width: 1fr;
        height: 16;
        align: center middle;
        margin-bottom: 1;
        display: none;
    }
    NowPlayingHero #hero-art { width: 32; height: 16; }
    NowPlayingHero.-playing #hero-art-row { display: block; }
    NowPlayingHero .hero-title {
        width: 1fr; text-align: center; text-style: bold; color: $accent;
    }
    NowPlayingHero .hero-artist {
        width: 1fr; text-align: center; color: $accent 80%;
    }
    NowPlayingHero .hero-album {
        width: 1fr; text-align: center; color: $accent 60%; margin-top: 1;
    }
    NowPlayingHero .hero-meta {
        width: 1fr; text-align: center; color: $accent 45%;
    }
    NowPlayingHero #hero-bio {
        width: 1fr; height: 1fr; margin-top: 1; scrollbar-size-vertical: 1;
    }
    NowPlayingHero .hero-bio {
        width: 1fr; color: $text-muted; text-align: left;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="hero-art-row"):
            yield TGPImage(id="hero-art")
        yield Label("", classes="hero-title")
        yield Label("", classes="hero-artist")
        yield Label("", classes="hero-album")
        yield Label("", classes="hero-meta")
        with VerticalScroll(id="hero-bio", can_focus=False):
            yield Static("", classes="hero-bio")

    def on_mount(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            self.query_one(TGPImage).styles.width = round(16 * cell.height / cell.width)
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "now_playing", self._on_now_playing, init=True)
        self.watch(app, "is_playing", self._on_is_playing, init=True)
        self.watch(app, "now_playing_album", self._on_details, init=True)
        self.watch(app, "now_playing_bio", self._on_bio, init=True)

    # ── reactive watchers ─────────────────────────────────────────────────────

    def _on_now_playing(self, track: object) -> None:
        if track and getattr(track, "image_url", None):
            self._fetch_art(track.image_url)  # type: ignore[union-attr]
        self._refresh_favorite()
        self._render_meta()

    def _on_is_playing(self, playing: bool) -> None:
        self.set_class(playing, "-playing")

    def _on_details(self, _: object) -> None:
        self._render_meta()

    def _on_bio(self, bio: str) -> None:
        self.query_one(".hero-bio", Static).update(strip_html(bio) if bio else "")
        self.query_one("#hero-bio", VerticalScroll).scroll_home(animate=False)

    # ── metadata render ───────────────────────────────────────────────────────

    def _render_meta(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        t = app.now_playing
        if not t:
            self.query_one(".hero-title", Label).update("No media playing")
            for cls in (".hero-artist", ".hero-album", ".hero-meta"):
                self.query_one(cls, Label).update("")
            return

        heart = (f"   {ICON_FAV}", "$accent") if self._favorite else ""
        self.query_one(".hero-title", Label).update(Content.assemble(t.display_title, heart))
        self.query_one(".hero-artist", Label).update(t.artist)

        album = app.now_playing_album
        album_parts = [t.album]
        if album and album.year:
            album_parts.append(str(album.year))
        self.query_one(".hero-album", Label).update("  ·  ".join(p for p in album_parts if p))

        meta_parts = [b for b in (album.genre, album.label) if b] if album else []
        self.query_one(".hero-meta", Label).update("  ·  ".join(meta_parts))

    @work
    async def _refresh_favorite(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        t = app.now_playing
        if not t:
            return
        ids = await app.ensure_favorite_ids()
        if self.is_mounted and app.now_playing is t:
            self._favorite = str(t.id) in ids
            self._render_meta()

    def action_scroll_up(self) -> None:
        self.query_one("#hero-bio", VerticalScroll).scroll_up(animate=False)

    def action_scroll_down(self) -> None:
        self.query_one("#hero-bio", VerticalScroll).scroll_down(animate=False)

    def action_scroll_page_up(self) -> None:
        self.query_one("#hero-bio", VerticalScroll).scroll_page_up(animate=False)

    def action_scroll_page_down(self) -> None:
        self.query_one("#hero-bio", VerticalScroll).scroll_page_down(animate=False)

    async def action_toggle_favorite(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        t = app.now_playing
        if not t:
            return
        self._favorite = await app.toggle_favorite(t)
        if self.is_mounted:
            self._render_meta()

    @work
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_mounted:
            try:
                self.query_one(TGPImage).image = img
            except NoMatches:
                pass


class QueueView(Widget):
    BINDINGS = [
        Binding("c", "clear_queue", "Clear queue", show=True),
        Binding("X", "clear_history", "Clear history", show=True),
    ]

    # Below this width the right pane is dropped and the list goes full-width.
    _NARROW_BELOW: int = 100

    DEFAULT_CSS = """
    QueueView { height: 1fr; layout: vertical; }
    QueueView #queue-body { height: 1fr; layout: horizontal; }
    QueueView #queue-list {
        width: 3fr;
        margin-right: 1;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
        border-subtitle-color: $accent 40%;
        border-subtitle-align: right;
    }
    QueueView #queue-list:focus {
        border: round $accent;
        border-title-color: $accent;
        border-subtitle-color: $accent;
    }
    /* Narrow terminals: hide the hero, list takes the full width. */
    QueueView.-narrow #now-hero { display: none; }
    QueueView.-narrow #queue-list { width: 1fr; margin-right: 0; }
    """

    _render_version: int = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="queue-body"):
            yield TrackListView(id="queue-list")
            yield NowPlayingHero(id="now-hero")

    def on_mount(self) -> None:
        self.query_one("#queue-list", ListView).border_title = "Queue"
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "queue_version", self._on_queue_changed, init=True)
        self.watch(app, "now_playing", self._on_now_playing_changed, init=False)
        self.watch(app, "is_paused", self._on_paused_changed, init=False)

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.width < self._NARROW_BELOW, "-narrow")

    def on_show(self) -> None:
        self.call_after_refresh(self.query_one("#queue-list", ListView).focus)

    def _on_queue_changed(self, _: int) -> None:
        self._render_version += 1
        self._refresh_list(self._render_version)

    def _on_now_playing_changed(self, _: object) -> None:
        self._render_version += 1
        self._refresh_list(self._render_version)

    def _on_paused_changed(self, paused: bool) -> None:
        # Flip the now-playing row's ▶/⏸ icon in place (no list re-render).
        try:
            self.query_one(NowPlayingListRow).set_paused(paused)
        except NoMatches:
            pass

    def action_clear_queue(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.clear_queue()

    def action_clear_history(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.clear_history()

    @work
    async def _refresh_list(self, version: int) -> None:
        if version != self._render_version:
            return
        app: QobitApp = self.app  # type: ignore[assignment]

        # Snapshot state and resolve async data while the list is still visible.
        now_playing = app.now_playing
        history = list(app._history)
        queue = list(app._play_queue)
        fav_ids = await app.ensure_favorite_ids()
        if version != self._render_version:
            return

        lv = self.query_one("#queue-list", ListView)
        await lv.clear()
        if version != self._render_version:
            return

        items: list[ListItem] = []
        now_idx: int | None = None

        # Recently Played region: history, then the now-playing track as its tail
        # (no separate section — it's the 'now' point of the timeline).
        if history:
            items.append(SectionHeader("Recently Played", count=len(history)))
            items.extend(HistoryTrackRow(t, str(t.id) in fav_ids) for t in history)
        if now_playing:
            now_idx = len(items)
            items.append(
                NowPlayingListRow(now_playing, app.is_paused, str(now_playing.id) in fav_ids)
            )

        if queue:
            items.append(
                SectionHeader("Up Next", count=len(queue), spaced=bool(history or now_playing))
            )
            items.extend(QueueTrackRow(t, i, str(t.id) in fav_ids) for i, t in enumerate(queue, 1))

        if not items:
            await lv.append(ListItem(Label("[dim]Queue is empty.[/dim]", markup=True)))
            lv.border_subtitle = ""
            return

        await lv.mount(*items)
        parts: list[str] = []
        if history:
            parts.append(f"{len(history)} played")
        if queue:
            parts.append(f"{len(queue)} queued")
        lv.border_subtitle = "  ·  ".join(parts)

        # Park the cursor on the now-playing row (PageUp walks into history,
        # PageDown down the queue) and centre it; fall back to the top.
        if now_idx is not None:
            lv.index = now_idx
            self.call_after_refresh(
                lambda: lv.scroll_to_widget(items[now_idx], animate=False, center=True)
            )
        if self.display:
            self.call_after_refresh(lv.focus)

    @on(ListView.Selected, "#queue-list")
    def _on_queue_selected(self, event: ListView.Selected) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        if isinstance(event.item, QueueTrackRow):
            lv = self.query_one("#queue-list", ListView)
            rows = list(lv.query(QueueTrackRow))
            idx = rows.index(event.item)
            remaining = [r.track for r in rows[idx + 1 :]]
            app.play_track(event.item.track, queue=remaining)
        elif isinstance(event.item, HistoryTrackRow):
            # Replay a past track as a one-off; leave Up Next untouched.
            app.play_track(event.item.track)
        self.call_after_refresh(event.list_view.focus)
