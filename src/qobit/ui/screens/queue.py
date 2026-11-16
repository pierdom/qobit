from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Literal

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
from .._now_playing import (
    GUTTER_NUM,
    heart_suffix,
    meta_indent,
    plain_if_highlighted,
    playing_content,
    playing_icon,
    track_live,
)
from .._utils import readout, status_line, strip_html
from ..widgets.art import Art
from ..widgets.lists import TrackListView
from ..widgets.transport import signal_parts

if TYPE_CHECKING:
    from ..app import QobitApp


# One gutter for the whole timeline (↺ history, ▶ now, numbered Up Next), so
# every row's text starts in the same column.
_GUTTER = GUTTER_NUM


def _meta(t: Track) -> str:
    return f"{meta_indent(_GUTTER)}{t.album}  ·  {t.duration_str}"


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
    SectionHeader Label { width: 1fr; color: $accent; text-style: bold; }
    """

    # Arbitrary placeholder width for the single frame between compose() (no
    # layout info yet) and the on_resize that always follows once this row is
    # actually laid out — never shown long enough to need to be exact.
    _INITIAL_WIDTH_GUESS = 56

    def __init__(self, title: str, count: int | None = None, spaced: bool = False) -> None:
        super().__init__(disabled=True)
        self.section = title
        self._count = count
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

    def set_count(self, count: int, spaced: bool) -> None:
        self.set_class(spaced, "-spaced")
        if count == self._count:
            return
        self._count = count
        with contextlib.suppress(NoMatches):
            self.query_one(Label).update(self._line(self._width))

    def _line(self, width: int) -> Text:
        # Uppercase rule: the timeline reads as a console readout.
        left = f"── {self.section.upper()} "
        right = f" {self._count} ──" if self._count is not None else "──"
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
    TimelineRow.-next .row-meta { color: $text-muted; }
    TimelineRow.-history .row-title { color: $text-muted; }
    TimelineRow.-history .row-meta { color: $text-muted 60%; }
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

    def _primary(self) -> Content:
        t = self.track
        lead = {
            "history": "↺",
            "now": playing_icon(_GUTTER, self._is_paused),
            "next": f"{self._number}.",
        }[self.role]
        text = f"{t.artist} — {t.display_title}"
        return playing_content(
            lead, text, False, False, heart_suffix(self._favorite), width=_GUTTER
        )

    def _refresh_title(self) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one(".row-title", Label).update(plain_if_highlighted(self, self._primary()))

    def watch_highlighted(self, value: bool) -> None:
        super().watch_highlighted(value)  # ListItem: the -highlight class
        if self.role is not None:
            self._refresh_title()

    def compose(self) -> ComposeResult:
        yield Label(self._primary(), classes="row-title")
        yield Label(_meta(self.track), classes="row-meta", markup=False)

    def set_paused(self, paused: bool) -> None:
        self._is_paused = paused
        self._refresh_title()

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self._refresh_title()


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
        /* Round like every other panel in qobit. */
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
    /* Whenever a track is loaded, also paused or restored after a restart. */
    NowPlayingHero.-has-track #hero-art-row { display: block; }
    NowPlayingHero .hero-title {
        width: 1fr; text-align: center; text-style: bold; color: $accent;
    }
    NowPlayingHero .hero-artist {
        width: 1fr; text-align: center; color: $accent 80%;
    }
    NowPlayingHero .hero-album {
        width: 1fr; text-align: center; color: $accent 75%; margin-top: 1;
    }
    /* $text-muted, not a fainter accent: $accent 45% was 2.8–3.2:1. */
    NowPlayingHero .hero-meta {
        width: 1fr; text-align: center; color: $text-muted;
    }
    NowPlayingHero .hero-quality {
        width: 1fr; text-align: center; color: $accent; margin-top: 1;
    }
    NowPlayingHero .hero-lamp { width: 1fr; text-align: center; }
    /* One line each, cut with an ellipsis: a centred line that wrapped
       ("Alternativa & Indie · Recordings" / "Catalogue") read as two items. */
    NowPlayingHero Label { text-wrap: nowrap; text-overflow: ellipsis; }
    NowPlayingHero #hero-bio {
        width: 1fr; height: 1fr; margin-top: 1; scrollbar-size-vertical: 1;
    }
    NowPlayingHero .hero-bio {
        width: 1fr; color: $text-muted; text-align: left;
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
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            self.query_one(TGPImage).styles.width = round(16 * cell.height / cell.width)
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "now_playing", self._on_now_playing, init=True)
        self.watch(app, "is_playing", self._on_is_playing, init=True)
        self.watch(app, "now_playing_album", self._on_details, init=True)
        self.watch(app, "now_playing_bio", self._on_bio, init=True)
        self.watch(app, "quality_label", self._on_quality, init=True)
        self.watch(app, "signal", self._on_quality, init=False)
        self.watch(app, "volume", self._on_quality, init=False)
        self.watch(app, "theme", self._on_quality, init=False)
        # toggle_favorite bumps queue_version: keeps the heart right when the
        # track is (un)favourited from a list or the `i` menu.
        self.watch(app, "queue_version", lambda _: self._refresh_favorite(), init=False)

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
    _refresh_lock: asyncio.Lock | None = None
    _empty_item: ListItem | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="queue-body"):
            yield TrackListView(id="queue-list")
            yield NowPlayingHero(id="now-hero")

    def on_mount(self) -> None:
        self.query_one("#queue-list", ListView).border_title = readout("Queue")
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "queue_version", self._on_queue_changed, init=True)
        self.watch(app, "now_playing", self._on_now_playing_changed, init=False)
        self.watch(app, "is_paused", self._on_paused_changed, init=False)

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.width < self._NARROW_BELOW, "-narrow")

    def activate(self) -> None:
        """Called by QobitApp when this tab becomes active: first load, focus."""
        self.call_after_refresh(self.query_one("#queue-list", ListView).focus)

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
            fav_ids = await app.ensure_favorite_ids()
            if version != self._render_version:
                return
            await self._reconcile(app, fav_ids)

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
        timeline, cursor = app._timeline, app._cursor
        now_playing = timeline[cursor] if 0 <= cursor < len(timeline) else app.now_playing
        history = list(app._history)
        queue = list(app._play_queue)

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
            header.set_count(len(history), spaced=False)
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
            header.set_count(len(queue), spaced=bool(history or now_playing))
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

        await self._apply(lv, desired)

        parts: list[str] = []
        if history:
            parts.append(f"{len(history)} played")
        if queue:
            parts.append(f"{len(queue)} queued")
        lv.border_subtitle = "  ·  ".join(parts)

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
        self.call_after_refresh(event.list_view.focus)
