from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

if TYPE_CHECKING:
    from ..app import QobitApp


def _fmt(secs: float) -> str:
    h, r = divmod(int(max(0.0, secs)), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class TransportBar(Widget):
    """Playback progress bar with click-to-seek.  Mirrors tuidash PlaybackBar.

    Self-wiring: subscribes to QobitApp reactives on mount so any instance
    placed anywhere in the widget tree is always live."""

    class SeekTo(Message):
        def __init__(self, position: float) -> None:
            super().__init__()
            self.position = position

    label: reactive[str] = reactive("")
    position: reactive[float] = reactive(0.0)
    duration: reactive[float] = reactive(0.0)
    is_paused: reactive[bool] = reactive(False)

    DEFAULT_CSS = """
    TransportBar {
        height: 4;
        border: round $primary-lighten-2;
        border-title-color: $primary-lighten-2;
        border-title-style: bold;
        padding: 0 1;
    }
    TransportBar.-playing {
        border: round $accent;
        border-title-color: $accent;
    }
    TransportBar:hover {
        background: $boost;
    }
    """

    def on_mount(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "now_playing", self._on_now_playing, init=True)
        self.watch(app, "is_playing", self._on_is_playing, init=True)
        self.watch(app, "is_paused", self._on_is_paused, init=True)
        self.watch(app, "playback_pos", self._on_pos, init=True)
        self.watch(app, "playback_dur", self._on_dur, init=True)
        self.watch(app, "status_msg", self._on_status_msg, init=True)

    def _on_now_playing(self, track: object) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        if track:
            self.label = f"{track.artist} — {track.display_title}"  # type: ignore[union-attr]
            self.border_title = "⏸  Now Playing" if app.is_paused else "▶  Now Playing"
        else:
            self.label = ""
            self.border_title = ""

    def _on_is_playing(self, playing: bool) -> None:
        self.set_class(playing, "-playing")

    def _on_is_paused(self, paused: bool) -> None:
        self.is_paused = paused
        app: QobitApp = self.app  # type: ignore[assignment]
        if app.now_playing:
            self.border_title = "⏸  Now Playing" if paused else "▶  Now Playing"

    def _on_pos(self, pos: float) -> None:
        self.position = pos

    def _on_dur(self, dur: float) -> None:
        self.duration = dur

    def _on_status_msg(self, msg: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        if msg:
            self.label = msg
        elif app.now_playing:
            self.label = f"{app.now_playing.artist} — {app.now_playing.display_title}"
        else:
            self.label = ""

    # ── rendering ────────────────────────────────────────────────────────────

    def render(self) -> Group:
        w = self.content_size.width or 40

        if self.label:
            line1 = Text(self.label, no_wrap=True, overflow="ellipsis", style="bold")
        else:
            line1 = Text(
                "No media playing  ·  search above and press Enter",
                style="dim",
                no_wrap=True,
            )

        time_str = f" {_fmt(self.position)} / {_fmt(self.duration)}"
        bar_w = max(1, w - len(time_str))
        self._bar_w = bar_w  # store for seek calculation

        filled = (
            round(bar_w * min(self.position, self.duration) / self.duration)
            if self.duration > 0
            else 0
        )
        bar = Text(no_wrap=True)
        bar.append("█" * filled, style="$accent")
        bar.append("░" * (bar_w - filled), style="dim $accent")
        bar.append(time_str, style="dim")

        return Group(line1, bar)

    # ── mouse seek ───────────────────────────────────────────────────────────

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.capture_mouse()
        self._seek_from_x(event.x)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if event.button:
            self._seek_from_x(event.x)

    def on_mouse_up(self, _: events.MouseUp) -> None:
        self.release_mouse()

    def _seek_from_x(self, x: int) -> None:
        if self.duration <= 0:
            return
        # content starts at x=2 (1 border col + 1 padding col)
        bar_w = getattr(self, "_bar_w", self.content_size.width)
        ratio = max(0.0, min(1.0, (x - 2) / max(1, bar_w - 1)))
        self.post_message(self.SeekTo(ratio * self.duration))
