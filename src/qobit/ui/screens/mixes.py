from __future__ import annotations

import contextlib
import dataclasses
from datetime import date
from typing import TYPE_CHECKING, Literal

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Track
from ...store import load as load_state
from ...store import save as save_state
from .._now_playing import (
    GUTTER_NUM,
    NowPlayingRowMixin,
    NowPlayingViewMixin,
    heart_suffix,
    meta_indent,
    playing_content,
)
from .._utils import readout, status_line
from ..widgets.lists import TrackListView

if TYPE_CHECKING:
    from ..app import QobitApp

MixKind = Literal["daily", "weekly"]


def _cache_key(kind: str) -> str:
    today = date.today()
    if kind == "weekly":
        year, week, _ = today.isocalendar()
        return f"{year}-W{week:02d}"
    return str(today)


class MixCard(Widget, can_focus=True):
    """One of the two mix tiles. Focusable, so the keyboard reaches it (Tab,
    ←/→ between the two); Enter opens it, ↓ goes to the track list. Space
    stays the app's pause (it used to open the card too, hiding Pause from
    the footer on this tab)."""

    BINDINGS = [
        Binding("enter", "open", "Open mix", show=False),
        Binding("left", "sibling(-1)", show=False),
        Binding("right", "sibling(1)", show=False),
        Binding("down", "to_list", show=False),
    ]

    class Selected(Message):
        def __init__(self, kind: MixKind) -> None:
            super().__init__()
            self.kind = kind

    DEFAULT_CSS = """
    MixCard {
        height: 7;
        width: 1fr;
        padding: 1 2;
        border: round $accent 40%;
    }
    /* The open mix: accent border and name. The keyboard cursor (focus): the
       selection fill, like a selected grid card. No other background. */
    MixCard.-selected { border: round $accent; }
    MixCard .mix-name { text-style: bold; height: 2; }
    MixCard.-selected .mix-name { color: $qobit-accent-text; }
    MixCard:focus { border: round $accent; background: $qobit-select; }
    /* After .-selected .mix-name (same specificity: the later rule wins). */
    MixCard:focus .mix-name, MixCard:focus .mix-desc, MixCard:focus .mix-status {
        color: $qobit-select-text;
    }
    MixCard .mix-desc { color: $qobit-muted; height: 1; }
    MixCard .mix-status { color: $qobit-muted; height: 1; }
    """

    def __init__(self, kind: MixKind) -> None:
        super().__init__()
        self._kind = kind

    def compose(self) -> ComposeResult:
        name = "DailyQ" if self._kind == "daily" else "WeeklyQ"
        every = "day" if self._kind == "daily" else "week"
        yield Label(name, classes="mix-name")
        yield Label(f"Made for you  ·  new every {every}", classes="mix-desc")
        yield Label("", classes="mix-status")

    def update_status(self, track_count: int) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one(".mix-status", Label).update(f"{track_count} tracks")

    def on_click(self) -> None:
        self.post_message(MixCard.Selected(self._kind))

    def action_open(self) -> None:
        self.post_message(MixCard.Selected(self._kind))

    def action_sibling(self, step: int) -> None:
        cards = list(self.parent.query(MixCard)) if self.parent else []
        if self in cards:
            cards[(cards.index(self) + step) % len(cards)].focus()

    def action_to_list(self) -> None:
        with contextlib.suppress(NoMatches):
            self.screen.query_one("#mix-track-list").focus()


class MixTrackRow(NowPlayingRowMixin, ListItem):
    DEFAULT_CSS = """
    MixTrackRow { height: 2; padding: 0 1; }
    MixTrackRow Label { width: 1fr; }
    MixTrackRow .row-title { text-style: bold; }
    MixTrackRow .row-meta { color: $qobit-muted; }
    """

    def __init__(self, track: Track, number: int, favorite: bool = False) -> None:
        super().__init__()
        self.track = track
        self._number = number
        self._favorite = favorite

    def _primary(self) -> Content:
        t = self.track
        heart = heart_suffix(self._favorite)
        body = f"{t.artist} — {t.display_title}"
        return playing_content(
            f"{self._number}.", body, self._is_playing, self._is_paused, heart, width=GUTTER_NUM
        )

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(self._primary(), classes="row-title")
        yield Label(
            f"{meta_indent(GUTTER_NUM)}{t.album}  ·  {t.duration_str}",
            classes="row-meta",
            markup=False,
        )

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = favorite
        self._refresh_primary()


class MixesView(NowPlayingViewMixin, Widget):
    BINDINGS = [
        Binding("d", "load_daily", "DailyQ", show=True),
        Binding("w", "load_weekly", "WeeklyQ", show=True),
    ]

    DEFAULT_CSS = """
    MixesView {
        height: 1fr;
        layout: vertical;
    }
    MixesView #mix-cards {
        height: 9;
        layout: horizontal;
        padding: 1 0 0 0;
    }
    /* Gutter before the second card only (there are exactly two: daily,
       weekly) — a trailing margin on the last one too would leave the row
       short of #mix-track-list's right edge below it. */
    MixesView MixCard:first-of-type {
        margin-right: 2;
    }
    MixesView #mix-track-list {
        height: 1fr;
        border: round $accent 40%;
        border-title-color: $qobit-muted;
        border-title-style: bold;
        border-subtitle-color: $qobit-muted;
        border-subtitle-align: right;
        padding: 0 1;
        margin-bottom: 1;
    }
    MixesView #mix-track-list:focus {
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-subtitle-color: $qobit-accent-text;
    }
    """

    _active_kind: str | None = None
    _tracks: list[Track]
    _render_version: int = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._tracks = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="mix-cards"):
            yield MixCard("daily")
            yield MixCard("weekly")
        yield TrackListView(id="mix-track-list")

    def on_mount(self) -> None:
        tl = self.query_one("#mix-track-list", TrackListView)
        tl.border_title = readout("Mixes")
        tl.border_subtitle = "d = DailyQ  ·  w = WeeklyQ"
        self._refresh_card_statuses()
        self._wire_now_playing(MixTrackRow, "#mix-track-list")

    def activate(self) -> None:
        """Called by QobitApp when this tab becomes active: first load, focus."""
        if self._tracks:
            self.call_after_refresh(self.query_one("#mix-track-list", TrackListView).focus)
        else:  # nothing open yet: start on the DailyQ card
            self.call_after_refresh(self.query(MixCard).first().focus)

    def _refresh_card_statuses(self) -> None:
        cache = load_state("mixes_cache.json")
        for card in self.query(MixCard):
            entry = cache.get(card._kind, {})
            if entry.get("key") == _cache_key(card._kind) and entry.get("tracks"):
                card.update_status(len(entry["tracks"]))

    # ── key bindings ─────────────────────────────────────────────────────────

    def action_load_daily(self) -> None:
        self._select_mix("daily")

    def action_load_weekly(self) -> None:
        self._select_mix("weekly")

    @on(MixCard.Selected)
    def _on_card_selected(self, event: MixCard.Selected) -> None:
        self._select_mix(event.kind)

    # ── mix selection + loading ───────────────────────────────────────────────

    def _select_mix(self, kind: str) -> None:
        for card in self.query(MixCard):
            card.set_class(card._kind == kind, "-selected")

        if self._active_kind == kind and self._tracks:
            self.query_one("#mix-track-list", TrackListView).focus()
            return

        self._active_kind = kind
        self._render_version += 1
        version = self._render_version

        cache = load_state("mixes_cache.json")
        entry = cache.get(kind, {})
        if entry.get("key") == _cache_key(kind) and entry.get("tracks"):
            tracks = [Track(**t) for t in entry["tracks"]]
            self._tracks = tracks
            self._load_cached(tracks, kind, version)
        else:
            tl = self.query_one("#mix-track-list", TrackListView)
            tl.border_title = readout("DailyQ" if kind == "daily" else "WeeklyQ")
            tl.border_subtitle = "generating…"
            self._generate(kind, version)

    @work
    async def _load_cached(self, tracks: list[Track], kind: str, version: int) -> None:
        tl = self.query_one("#mix-track-list", TrackListView)
        try:
            await tl.remove_children()
        except Exception:
            return
        await self._mount_tracks(tl, tracks, kind, version)

    @work
    async def _generate(self, kind: str, version: int) -> None:
        tl = self.query_one("#mix-track-list", TrackListView)
        try:
            await tl.remove_children()
        except Exception:
            return

        app: QobitApp = self.app  # type: ignore[assignment]
        tracks = await app.generate_mix()

        if version != self._render_version or not self.is_attached:
            return

        if not tracks:
            try:
                tl.border_subtitle = ""
                await tl.mount(ListItem(status_line("No mix yet: play some tracks first.")))
            except Exception:
                pass
            return

        cache = load_state("mixes_cache.json")
        cache[kind] = {
            "key": _cache_key(kind),
            "tracks": [dataclasses.asdict(t) for t in tracks],
        }
        save_state("mixes_cache.json", cache)

        for card in self.query(MixCard):
            if card._kind == kind:
                card.update_status(len(tracks))

        self._tracks = tracks
        await self._mount_tracks(tl, tracks, kind, version)

    async def _mount_tracks(
        self, tl: TrackListView, tracks: list[Track], kind: str, version: int
    ) -> None:
        if version != self._render_version or not self.is_attached:
            return
        name = "DailyQ" if kind == "daily" else "WeeklyQ"
        refreshed = "today" if kind == "daily" else "this week"
        tl.border_title = readout(f"{name}  ·  {len(tracks)} tracks")
        tl.border_subtitle = f"refreshed {refreshed}"

        fav_ids: set[str] = set()
        with contextlib.suppress(Exception):
            fav_ids = await self.app.ensure_favorite_ids()  # type: ignore[attr-defined]

        if version != self._render_version or not self.is_attached:
            return

        rows = [MixTrackRow(t, i, favorite=t.id in fav_ids) for i, t in enumerate(tracks, 1)]
        self._apply_now_playing(rows)
        try:
            await tl.mount(*rows)
        except Exception:
            return

        if self.is_attached:
            self.call_after_refresh(tl.focus)

    # ── track play ───────────────────────────────────────────────────────────

    @on(ListView.Selected, "#mix-track-list")
    def _on_track_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, MixTrackRow):
            return
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#mix-track-list", TrackListView)
        rows = list(lv.query(MixTrackRow))
        idx = rows.index(event.item)
        queue = [r.track for r in rows[idx + 1 :]]
        app.play_track(event.item.track, queue=queue)
        self.call_after_refresh(lv.focus)
