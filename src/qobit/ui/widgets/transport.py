from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget


def _fmt(secs: float) -> str:
    h, r = divmod(int(max(0.0, secs)), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class TransportBar(Widget):
    """Playback progress bar with click-to-seek.  Mirrors tuidash PlaybackBar."""

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
