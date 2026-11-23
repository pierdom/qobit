from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Literal

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.events import Resize
from textual.geometry import Size
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static
from textual_image.widget import TGPImage

from ...qobuz.models import Track
from .._images import fetch_image
from .._now_playing import (
    GUTTER_NUM,
    gutter,
    heart_suffix,
    meta_indent,
    plain_if_highlighted,
    playing_content,
    playing_icon,
    track_live,
)
from .._utils import focus_later, readout, status_line, strip_html
from ..widgets.art import Art, square_width
from ..widgets.lists import TrackListView
from ..widgets.lyrics import LyricsView
from ..widgets.transport import signal_parts

if TYPE_CHECKING:
    from ..app import QobitApp


# One gutter for the whole timeline (↺ history, ▶ now, numbered Up Next), so
# every row's text starts in the same column.
_GUTTER = GUTTER_NUM

# Up Next rows mounted past the playing one; the rest mount as the cursor or
# the scroll nears them (see QueueView._reconcile).
_UP_NEXT_PAGE = 60


def _meta(t: Track) -> str:
    return f"{meta_indent(_GUTTER)}{t.album}"


def _total(tracks: list[Track]) -> str:
    """Playing time of ``tracks`` (m:ss, h:mm:ss from an hour), "" if unknown."""
    secs = sum(t.duration for t in tracks)
    h, rest = divmod(secs, 3600)
    m, sec = divmod(rest, 60)
    return "" if not secs else f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class SectionHeader(ListItem):
    """A non-selectable, full-width divider labelling a queue section.

    The rule spans the list width (recomputed on resize) with the title on the
    left and an optional count on the right: ``── Up Next ──────── 35 ──``.
    ``set_count`` updates it in place (the list is reconciled, not rebuilt)."""

    DEFAULT_CSS = """
    /* No background: the rule is the divider. A $surface band showed only in
       the 1-col padding at each end under a transparent background (the
       Label goes ansi_default there), and lists sit on their panel anyway. */
    SectionHeader { height: 1; padding: 0 1; background: transparent; }
    SectionHeader.-spaced { margin-top: 1; }
    SectionHeader Label { width: 1fr; color: $qobit-accent-text; text-style: bold; }
    """

    # Arbitrary placeholder width for the single frame between compose() (no
    # layout info yet) and the on_resize that always follows once this row is
    # actually laid out — never shown long enough to need to be exact.
    _INITIAL_WIDTH_GUESS = 56

    def __init__(self, title: str, count: int | None = None, spaced: bool = False) -> None:
        super().__init__(disabled=True)
        self.section = title
        self._count = count
        self._total = ""
        self._width = self._INITIAL_WIDTH_GUESS
        self.set_class(spaced, "-spaced")

    def compose(self) -> ComposeResult:
        yield Label(self._line(self._width))

    def on_resize(self, event: Resize) -> None:
        # event.size is the border-box width; subtract our own horizontal padding
        # to get the Label's actual content width.
        pad = self.styles.padding.left + self.styles.padding.right
        self._width = event.size.width - pad
        self.query_one(Label).update(self._line(self._width))

    def set_count(self, count: int, spaced: bool, total: str = "") -> None:
        self.set_class(spaced, "-spaced")
        if (count, total) == (self._count, self._total):
            return
        self._count, self._total = count, total
        with contextlib.suppress(NoMatches):
            self.query_one(Label).update(self._line(self._width))

    def _line(self, width: int) -> Text:
        # Uppercase rule: the timeline reads as a console readout.
        left = f"── {self.section.upper()} "
        if self._count is None:
            right = "──"
        else:
            right = f" {self._count}{'  ·  ' + self._total if self._total else ''} ──"
        # `width` is the Label's content width; -1 leaves slack for the list's
        # scrollbar gutter so the rule never wraps onto a second line.
        fill = max(2, width - len(left) - len(right) - 1)
        return Text(left + "─" * fill + right, no_wrap=True, overflow="crop")


Role = Literal["history", "now", "next"]


class TimelineRow(ListItem):
    """One track of the Playback timeline. Its ``role`` says where it sits:
    ``history`` (Recently Played, muted ``↺``), ``now`` (accent, ▶/⏸) or
    ``next`` (Up Next, numbered). ``configure`` changes role, number, heart
    and pause state in place, so when a track ends the same row just moves
    from Up Next to now to history instead of the list being rebuilt."""

    DEFAULT_CSS = """
    TimelineRow { height: 2; padding: 0 1; }
    TimelineRow Label { width: 1fr; }
    TimelineRow.-next .row-title { text-style: bold; }
    TimelineRow.-next .row-meta { color: $qobit-muted; }
    TimelineRow.-history .row-title { color: $qobit-muted; }
    TimelineRow.-history .row-meta { color: $qobit-muted; }  /* 60% was 4.1–4.3:1 */
    /* Accent text in its readable variant ($qobit-accent-text: darker in a
       light theme), on a tint that's none in ANSI themes (they can't blend). */
    TimelineRow.-now { background: $qobit-now; }
    TimelineRow.-now .row-title { text-style: bold; color: $qobit-accent-text; }
    TimelineRow.-now .row-meta { color: $qobit-accent-text; }
    """

    role: Role | None = None  # set by the first configure()
    timeline_index: int = 0

    def __init__(self, track: Track) -> None:
        super().__init__()
        self.track = track
        self._number = 0
        self._favorite = False
        self._is_paused = False
        self._width = 0  # the title line's width, once laid out (see _with_time)

    def configure(
        self,
        role: Role,
        *,
        timeline_index: int,
        number: int = 0,
        favorite: bool = False,
        paused: bool = False,
    ) -> None:
        self.timeline_index = timeline_index
        if (role, number, favorite, paused) == (
            self.role,
            self._number,
            self._favorite,
            self._is_paused,
        ):
            return
        for r in ("history", "now", "next"):
            self.set_class(r == role, f"-{r}")
        self.role, self._number, self._favorite, self._is_paused = (
            role,
            number,
            favorite,
            paused,
        )
        track_live(self, role == "now")
        self._refresh_title()

    def _refresh_primary(self) -> None:  # the beat's redraw (_now_playing.tick)
        self._refresh_title()

    def _primary(self) -> tuple[Content, tuple[str, str] | str]:
        """The title (gutter, artist — title) and the ♥ that follows it."""
        t = self.track
        lead = {
            "history": "↺",
            "now": playing_icon(_GUTTER, self._is_paused),
            "next": f"{self._number}.",
        }[self.role]
        text = f"{t.artist} — {t.display_title}"
        heart = heart_suffix(self._favorite)
        if self.role == "next":  # the number recedes behind the title
            return Content.assemble((gutter(lead, _GUTTER), "$qobit-muted"), text), heart
        return playing_content(lead, text, False, False, width=_GUTTER), heart

    def _with_time(self, title: Content, heart: tuple[str, str] | str = "") -> Content:
        """The title line with the length (and HI-RES) at its right edge: drawn
        into the line, so the album line below keeps its whole width. The title
        gives way with an ellipsis, never the ♥ after it nor the length. Unknown
        width (not laid out yet): as is."""
        suffix = Content.assemble(heart)
        if self._width <= 0:
            return Content.assemble(title, suffix)
        time = self.track.duration_str
        badge = "HI-RES  " if self.track.hires else ""
        room = max(8, self._width - len(badge) - len(time) - 2)
        if title.cell_length + suffix.cell_length > room:
            title = title.truncate(max(1, room - suffix.cell_length), ellipsis=True)
        left = Content.assemble(title, suffix)
        left = left.pad_right(max(0, room - left.cell_length))
        muted = "$qobit-muted" if self.role == "next" else ""
        parts: list[tuple[str, str] | str] = [left, "  "]
        if badge:
            parts.append((badge, "bold $qobit-hires"))
        parts.append((time, muted))
        return Content.assemble(*parts)

    def _refresh_title(self) -> None:
        with contextlib.suppress(NoMatches):
            # One line (nowrap): no layout pass (see NowPlayingRowMixin).
            title = plain_if_highlighted(self, self._with_time(*self._primary()))
            self.query_one(".row-title", Label).update(title, layout=False)

    def on_resize(self, event: Resize) -> None:
        width = self.content_size.width
        if width != self._width:
            self._width = width
            self._refresh_title()

    def watch_highlighted(self, value: bool) -> None:
        super().watch_highlighted(value)  # ListItem: the -highlight class
        if self.role is not None:
            self._refresh_title()

    def compose(self) -> ComposeResult:
        yield Label(self._with_time(*self._primary()), classes="row-title")
        yield Label(_meta(self.track), classes="row-meta", markup=False)

    def set_paused(self, paused: bool) -> None:
        self._is_paused = paused
        self._refresh_title()

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self._refresh_title()


# Now Playing panel rows: border + padding, then the text under the cover
# (title, artist, album + gap, genre/label, resolution + gap, lamp) and the
# gap below the cover. The cover gets what's left, up to _ART_ROWS.
_CHROME_ROWS = 4
_TEXT_ROWS = 9
_ART_ROWS = 16
_ART_MIN_ROWS = 4


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
        width: 1fr;
        height: 1fr;
        /* Round like every other panel in qobit. */
        border: round $accent 40%;
        layout: vertical;
        align-horizontal: center;
        padding: 1 2;
    }
    NowPlayingHero:focus {
        border: round $accent;
        border-subtitle-color: $qobit-accent-text;
    }
    NowPlayingHero {
        border-subtitle-color: $qobit-muted;
        border-subtitle-align: right;
    }
    NowPlayingHero #hero-art-row {
        width: 1fr;
        height: 16;
        align: center middle;
        margin-bottom: 1;
        display: none;
    }
    NowPlayingHero #hero-art { width: 32; height: 16; }
    /* Whenever a track is loaded, also paused or restored after a restart. */
    NowPlayingHero.-has-track #hero-art-row { display: block; }
    /* Too short for a recognisable cover (see _fit_art): text only. */
    NowPlayingHero.-has-track.-no-art #hero-art-row { display: none; }
    NowPlayingHero .hero-title {
        width: 1fr; text-align: center; text-style: bold; color: $qobit-accent-text;
    }
    NowPlayingHero .hero-artist {
        width: 1fr; text-align: center; color: $qobit-accent-text;
    }
    NowPlayingHero .hero-album {
        width: 1fr; text-align: center; color: $qobit-accent-text; margin-top: 1;
    }
    /* $qobit-muted, not a fainter accent: $accent 45% was 2.8–3.2:1. */
    NowPlayingHero .hero-meta {
        width: 1fr; text-align: center; color: $qobit-muted;
    }
    NowPlayingHero .hero-quality {
        width: 1fr; text-align: center; color: $qobit-accent-text; margin-top: 1;
    }
    NowPlayingHero .hero-lamp { width: 1fr; text-align: center; }
    /* One line each, cut with an ellipsis: a centred line that wrapped
       ("Alternativa & Indie · Recordings" / "Catalogue") read as two items. */
    NowPlayingHero Label { text-wrap: nowrap; text-overflow: ellipsis; }
    NowPlayingHero #hero-bio {
        width: 1fr; height: 1fr; margin-top: 1; scrollbar-size-vertical: 1;
    }
    NowPlayingHero .hero-bio {
        width: 1fr; color: $qobit-muted; text-align: left;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="hero-art-row"):
            yield Art(id="hero-art")
        yield Label("", classes="hero-title")
        yield Label("", classes="hero-artist", markup=False)
        yield Label("", classes="hero-album", markup=False)
        yield Label("", classes="hero-meta", markup=False)
        yield Label("", classes="hero-quality", markup=False)
        yield Label("", classes="hero-lamp", markup=False)
        with VerticalScroll(id="hero-bio", can_focus=False):
            yield Static("", classes="hero-bio", markup=False)

    def on_mount(self) -> None:
        self._fit_art(self.size.height)
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "now_playing", self._on_now_playing, init=True)
        self.watch(app, "is_playing", self._on_is_playing, init=True)
        self.watch(app, "now_playing_album", self._on_details, init=True)
        self.watch(app, "now_playing_bio", self._on_bio, init=True)
        self.watch(app, "quality_label", self._on_quality, init=True)
        self.watch(app, "signal", self._on_quality, init=False)
        self.watch(app, "volume", self._on_quality, init=False)
        # After the refresh: theme_variables lag the theme reactive (see
        # TransportBar._on_theme).
        self.watch(app, "theme", lambda _: self.call_after_refresh(self._on_quality), init=False)
        # toggle_favorite bumps queue_version: keeps the heart right when the
        # track is (un)favourited from a list or the `i` menu.
        self.watch(app, "queue_version", lambda _: self._refresh_favorite(), init=False)

    def on_resize(self, event: Resize) -> None:
        self._fit_art(event.size.height)

    def _fit_art(self, height: int) -> None:
        """Size the cover to what the panel has left after its text, up to
        _ART_ROWS: at a fixed 16 rows it pushed the resolution and the lamp
        out of a short panel. Below _ART_MIN_ROWS the cover hides."""
        rows = max(0, min(_ART_ROWS, height - _CHROME_ROWS - _TEXT_ROWS))
        self.set_class(rows < _ART_MIN_ROWS, "-no-art")
        if rows < _ART_MIN_ROWS:
            return
        self.query_one("#hero-art-row").styles.height = rows
        art = self.query_one(TGPImage)
        art.styles.height = rows
        square_width(art, rows)

    # ── reactive watchers ─────────────────────────────────────────────────────

    def _on_now_playing(self, track: object) -> None:
        self.set_class(bool(track), "-has-track")
        if track:
            self.query_one(Art).placeholder = getattr(track, "album", "") or ""
        if track and getattr(track, "image_url", None):
            self._fetch_art(track.image_url)  # type: ignore[union-attr]
        self._refresh_favorite()
        self._render_meta()

    def _on_is_playing(self, playing: bool) -> None:
        self.set_class(playing, "-playing")

    def _on_details(self, _: object) -> None:
        self._render_meta()

    def _on_quality(self, _: object = None) -> None:
        # The transport's readout, a line each (one line didn't fit the panel).
        lamp, quality = signal_parts(self.app)  # type: ignore[arg-type]
        self.query_one(".hero-lamp", Label).update(lamp or "")
        self.query_one(".hero-quality", Label).update(quality or "")

    def _on_bio(self, bio: str) -> None:
        self.query_one(".hero-bio", Static).update(strip_html(bio) if bio else "")
        self.query_one("#hero-bio", VerticalScroll).scroll_home(animate=False)

    # ── metadata render ───────────────────────────────────────────────────────

    def _render_meta(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        t = app.now_playing
        if not t:
            self.query_one(".hero-title", Label).update("No media playing")
            for cls in (".hero-artist", ".hero-album", ".hero-meta", ".hero-quality", ".hero-lamp"):
                self.query_one(cls, Label).update("")
            return

        heart = heart_suffix(self._favorite)
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
        if self.is_attached and app.now_playing is t:
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
        if self.is_attached:
            self._render_meta()

    @work(exclusive=True, group="_fetch_art")  # a newer request cancels a stale one
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_attached:
            with contextlib.suppress(NoMatches):
                self.query_one(TGPImage).image = img


class QueueView(Widget):
    BINDINGS = [
        Binding("c", "clear_queue", "Clear queue", show=True),
        Binding("X", "clear_history", "Clear history", show=True),
    ]

    # Below this width the right pane is dropped and the list goes full-width.
    _NARROW_BELOW: int = 100
    # The right pane holds the hero and the lyrics. From _SIDE_MIN_W columns
    # they sit side by side; else from _STACK_MIN_H rows, one above the other;
    # else only one shows and `l` swaps them. Measured on this view, never on
    # the terminal: the chrome around it differs per screen.
    _SIDE_MIN_W: int = 140
    _STACK_MIN_H: int = 26

    DEFAULT_CSS = """
    QueueView { height: 1fr; layout: vertical; }
    QueueView #queue-body { height: 1fr; layout: horizontal; }
    QueueView #queue-list {
        width: 3fr;
        margin-right: 1;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-title-style: bold;
        border-subtitle-color: $qobit-muted;
        border-subtitle-align: right;
    }
    QueueView #queue-list:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    /* The right pane: the hero and the lyrics (see _relayout for which show).
       One above the other by default, side by side when -side. */
    QueueView #side { width: 2fr; height: 1fr; layout: vertical; }
    /* Two columns: list 60%, right pane 40%. Three: sized in cells by
       _proportion (list 40 / hero 30 / lyrics 30, the hero growing with width). */
    QueueView.-side #queue-list { width: 4fr; }
    QueueView.-side #side { width: 6fr; layout: horizontal; }
    QueueView.-side #now-hero { width: 1fr; min-width: 38; }
    QueueView.-side #lyrics-panel { width: 1fr; }
    QueueView #lyrics-panel { width: 1fr; height: 1fr; display: none; }
    QueueView.-lyrics #lyrics-panel { display: block; }
    QueueView.-hero-off #now-hero { display: none; }
    QueueView.-side #now-hero { margin-right: 1; }
    /* Narrow terminals: the list takes the full width. */
    QueueView.-narrow #side { display: none; }
    QueueView.-narrow #queue-list { width: 1fr; margin-right: 0; }
    """

    _render_version: int = 0
    _refresh_lock: asyncio.Lock | None = None
    _empty_item: ListItem | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="queue-body"):
            yield TrackListView(id="queue-list")
            with Container(id="side"):
                yield NowPlayingHero(id="now-hero")
                yield LyricsView(id="lyrics-panel")

    def on_mount(self) -> None:
        self.query_one("#queue-list", ListView).border_title = readout("Queue")
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "queue_version", self._on_queue_changed, init=True)
        self.watch(app, "lyrics_panel", lambda _: self._relayout(), init=False)
        self.watch(app, "lyrics_enabled", lambda _: self._relayout(), init=False)
        self.watch(app, "now_playing", self._on_now_playing_changed, init=False)
        self.watch(app, "is_paused", self._on_paused_changed, init=False)

    def _relayout(self, size: Size | None = None) -> None:
        """Which of the hero and the lyrics show, and how (see _SIDE_MIN_W)."""
        app: QobitApp = self.app  # type: ignore[assignment]
        width, height = size or self.size
        narrow = width < self._NARROW_BELOW
        on = app.lyrics_enabled
        side = on and not narrow and width >= self._SIDE_MIN_W
        stack = on and not narrow and not side and height >= self._STACK_MIN_H
        swapped = on and not narrow and not side and not stack and app.lyrics_panel
        self.set_class(narrow, "-narrow")
        self.set_class(side, "-side")
        self.set_class(stack, "-stack")
        self.set_class(side or stack or swapped, "-lyrics")  # the lyrics show
        self.set_class(swapped, "-hero-off")  # ... instead of the hero
        # Where they take turns, each says which key brings the other.
        turns = on and not narrow and not side and not stack
        self.query_one("#now-hero").border_subtitle = "l  lyrics" if turns else ""
        panel = self.query_one("#lyrics-panel", LyricsView)
        panel.hint = "l  now playing" if turns else ""
        panel.refresh_subtitle()
        focused = self.app.focused
        if swapped and focused is not None and focused.id == "now-hero":
            self.query_one("#queue-list", ListView).focus()  # the hero just went
        self.call_after_refresh(self._proportion)  # the cell widths follow -side

    def lyrics_swap(self) -> bool:
        """Whether the hero and the lyrics take turns here (`l` swaps them):
        there is room for the right pane but not for both."""
        on = self.app.lyrics_enabled  # type: ignore[attr-defined]
        return on and not any(self.has_class(c) for c in ("-narrow", "-side", "-stack"))

    def on_resize(self, event: Resize) -> None:
        self._relayout(event.size)
        self.call_after_refresh(self._proportion)

    def _proportion(self) -> None:
        """Side by side, the three panes are sized in cells from the width.
        Near _SIDE_MIN_W: list 40%, hero 30%, lyrics 30%. Wider, the list
        shrinks (its rows are short) and the hero takes it, up to 30/40/30
        at 200 columns; the lyrics stay at 30% (long lines read worse)."""
        lst = self.query_one("#queue-list")
        hero = self.query_one("#now-hero")
        panel = self.query_one("#lyrics-panel")
        if not self.has_class("-side"):
            lst.styles.width = hero.styles.width = panel.styles.width = None
            return
        room = self.query_one("#queue-body").size.width - 2  # two gaps
        t = min(1.0, max(0.0, (room + 2 - self._SIDE_MIN_W) / 60))
        widths = {lst: round(room * (0.40 - 0.10 * t)), panel: round(room * 0.30)}
        widths[hero] = max(38, room - widths[lst] - widths[panel])
        for w, cells in widths.items():
            if w.styles.width is None or w.styles.width.value != cells:
                w.styles.width = cells

    def activate(self) -> None:
        """Called by QobitApp when this tab becomes active: first load, focus."""
        focus_later(self, self.query_one("#queue-list", ListView).focus)

    def _on_queue_changed(self, _: int) -> None:
        self._render_version += 1
        self._refresh_list(self._render_version)

    def _on_now_playing_changed(self, _: object) -> None:
        self._render_version += 1
        self._refresh_list(self._render_version)

    def _on_paused_changed(self, paused: bool) -> None:
        # Flip the now-playing row's ▶/⏸ icon in place (no list re-render).
        for row in self.query(TimelineRow):
            if row.role == "now":
                row.set_paused(paused)

    def action_clear_queue(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.clear_queue()

    def action_clear_history(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        app.clear_history()

    @work
    async def _refresh_list(self, version: int) -> None:
        # One reconcile at a time: each reads the list as the previous one left
        # it. A request overtaken by a newer one (bursts of queue_version
        # bumps) skips straight to the newest.
        if self._refresh_lock is None:
            self._refresh_lock = asyncio.Lock()
        async with self._refresh_lock:
            if version != self._render_version:
                return
            app: QobitApp = self.app  # type: ignore[assignment]
            # The list doesn't wait for the favourites: fetching a whole
            # library kept the queue blank for seconds at startup. Rows show
            # no hearts until they arrive, then one more reconcile adds them.
            fav_ids = app.favorite_ids
            if fav_ids is None:
                self._hearts_when_loaded()
            # Gone mid-way: logout rebuilds every view (App.recompose) while
            # a reconcile, started by the favourites arriving, may be running.
            with contextlib.suppress(NoMatches):
                await self._reconcile(app, fav_ids or set())

    @work(exclusive=True, group="_hearts_when_loaded")
    async def _hearts_when_loaded(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        await app.ensure_favorite_ids()
        # Not loaded (the fetch failed): no retry loop; the next change retries.
        if app.favorite_ids is not None and self.is_attached:
            self._on_queue_changed(app.queue_version)

    async def _reconcile(self, app: QobitApp, fav_ids: set[str]) -> None:
        """Bring the list in line with the timeline without clearing it.

        Rows are matched to tracks (by track id, in order, so repeats work),
        reconfigured in place and moved only when out of order; only tracks
        that are new get mounted and only rows that left get removed. When a
        track ends, that is one row moving and three rows changing role,
        instead of the whole list being remounted (which flickered and reset
        the scroll position)."""
        lv = self.query_one("#queue-list", ListView)
        # The "now" row comes from the cursor, like history and Up Next, not
        # from app.now_playing: advance/jump move the cursor at once, but
        # now_playing changes only once the stream URL resolves. Reading it
        # in between showed the old track twice and the next one missing, then
        # moved the rows back a moment later.
        now_playing = app.current_track or app.now_playing
        history = app.history  # a fresh list each time
        queue = app.up_next

        # Where the user was: a row other than the playing one keeps the cursor
        # (a track ending shouldn't lose your place in Up Next); on the playing
        # row, or nowhere, it follows playback.
        held = lv.highlighted_child
        follow = not isinstance(held, TimelineRow) or held.role == "now"
        first_render = not any(isinstance(c, TimelineRow) for c in lv.children)

        pool: defaultdict[str, deque[TimelineRow]] = defaultdict(deque)
        headers: dict[str, SectionHeader] = {}
        for child in lv.children:
            if isinstance(child, TimelineRow):
                pool[str(child.track.id)].append(child)
            elif isinstance(child, SectionHeader):
                headers[child.section] = child

        def row_for(t: Track) -> TimelineRow:
            rows = pool.get(str(t.id))
            row = rows.popleft() if rows else TimelineRow(t)
            row.track = t  # Connect mirrors rebuild Track objects: keep the newest
            return row

        desired: list[ListItem] = []
        ti = 0  # timeline index: history, then now, then Up Next, in order
        now_row: TimelineRow | None = None
        if history:
            header = headers.get("Recently Played") or SectionHeader("Recently Played")
            header.set_count(len(history), spaced=False, total=_total(history))
            desired.append(header)
            for t in history:
                row = row_for(t)
                row.configure("history", timeline_index=ti, favorite=str(t.id) in fav_ids)
                desired.append(row)
                ti += 1
        if now_playing:
            now_row = row_for(now_playing)
            now_row.configure(
                "now",
                timeline_index=ti,
                favorite=str(now_playing.id) in fav_ids,
                paused=app.is_paused,
            )
            desired.append(now_row)
            ti += 1
        if queue:
            header = headers.get("Up Next") or SectionHeader("Up Next")
            header.set_count(len(queue), spaced=bool(history or now_playing), total=_total(queue))
            desired.append(header)
            for n, t in enumerate(queue, 1):
                row = row_for(t)
                row.configure("next", timeline_index=ti, number=n, favorite=str(t.id) in fav_ids)
                desired.append(row)
                ti += 1
        if not desired:
            if self._empty_item is None or not self._empty_item.is_attached:
                self._empty_item = ListItem(
                    status_line("Queue is empty. Pick a track anywhere and press Enter.")
                )
            desired.append(self._empty_item)

        # Mount through the playing row and a page of Up Next; the rest waits
        # in the list's backlog (PagedListView) until the cursor or the
        # scroll gets near it. Every mounted row costs on each cursor move
        # and repaint (Textual maps them all), and a long playlist put
        # hundreds in Up Next. Never fewer than are mounted already (rows the
        # user scrolled to stay), and always through the row the cursor holds.
        limit = max(
            _UP_NEXT_PAGE + (desired.index(now_row) if now_row else 0),
            len(lv.children),
            desired.index(held) + 1 if held in desired else 0,
        )
        lv._backlog = desired[limit:]  # type: ignore[attr-defined]
        await self._apply(lv, desired[:limit])

        # Where you are in the timeline (the section rules carry counts and times).
        position = app.queue_position
        lv.border_subtitle = f"{position[0]} of {position[1]}" if now_playing and position else ""

        target = now_row if follow or held not in desired else held
        self._place_cursor(lv, target, center=first_render)

    @staticmethod
    async def _apply(lv: ListView, desired: list[ListItem]) -> None:
        """Make ``lv``'s children exactly ``desired``: walk it in order, moving
        rows already mounted, mounting runs of new ones in one call, and
        removing what's left over at the end (so nothing disappears before
        its replacement is in place)."""
        current = list(lv.children)
        mounted = {id(w) for w in current}
        k = 0
        while k < len(desired):
            w = desired[k]
            if k < len(current) and current[k] is w:
                k += 1
                continue
            if id(w) in mounted:
                lv.move_child(w, before=current[k])
                current.remove(w)
                current.insert(k, w)
                k += 1
                continue
            run = []
            j = k
            while j < len(desired) and id(desired[j]) not in mounted:
                run.append(desired[j])
                j += 1
            if k < len(current):
                await lv.mount(*run, before=current[k])
            else:
                await lv.mount(*run)
            current[k:k] = run
            mounted.update(id(w) for w in run)
            k = j
        if stale := current[len(desired) :]:
            await lv.remove_children(stale)

    def _place_cursor(self, lv: ListView, target: ListItem | None, center: bool) -> None:
        # Rows moved under a numeric index: drop any highlight left on the
        # wrong row, then point the index at the target (re-set even when the
        # number is unchanged, so the right row gets highlighted).
        for child in lv.children:
            if isinstance(child, ListItem) and child is not target:
                child.highlighted = False
        if target is None or target not in lv.children:
            return
        idx = list(lv.children).index(target)
        if lv.index == idx:
            lv.index = None
        lv.index = idx
        if center:  # first fill: put the playing track mid-list
            self.call_after_refresh(lambda: lv.scroll_to_widget(target, animate=False, center=True))

    @on(ListView.Selected, "#queue-list")
    def _on_queue_selected(self, event: ListView.Selected) -> None:
        # Every track row carries its timeline index; selecting any of them —
        # history, now-playing, or up-next — just moves the cursor there. Jumping
        # back resumes the line (the tracks in between return to Up Next);
        # jumping forward leaves the skipped-over tracks under Recently Played.
        app: QobitApp = self.app  # type: ignore[assignment]
        index = getattr(event.item, "timeline_index", None)
        if index is not None:
            app.jump_to(index)
        focus_later(self, event.list_view.focus)
