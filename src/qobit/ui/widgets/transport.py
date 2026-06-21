from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from .._images import fetch_image

if TYPE_CHECKING:
    from ..app import QobitApp


def _fmt(secs: float) -> str:
    h, r = divmod(int(max(0.0, secs)), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _TransportContent(Widget):
    """Label + album + progress bar; rendered to the right of the album art."""

    DEFAULT_CSS = """
    _TransportContent {
        width: 1fr;
        height: 1fr;
    }
    """

    label: reactive[str] = reactive("")
    album: reactive[str] = reactive("")
    position: reactive[float] = reactive(0.0)
    duration: reactive[float] = reactive(0.0)

    _bar_w: int = 40

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

        line2 = Text(self.album, no_wrap=True, overflow="ellipsis", style="dim")

        time_str = f" {_fmt(self.position)} / {_fmt(self.duration)}"
        bar_w = max(1, w - len(time_str))
        self._bar_w = bar_w

        filled = (
            round(bar_w * min(self.position, self.duration) / self.duration)
            if self.duration > 0
            else 0
        )
        bar = Text(no_wrap=True)
        bar.append("█" * filled, style="$accent")
        bar.append("░" * (bar_w - filled), style="dim $accent")
        bar.append(time_str, style="dim")

        return Group(line1, line2, bar)

    # ── mouse interaction ─────────────────────────────────────────────────────

    _seeking: bool = False

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.y >= 2:
            self._seeking = True
            self.capture_mouse()
            self._seek_from_x(event.x)
        else:
            app: QobitApp = self.app  # type: ignore[assignment]
            app.action_pause()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._seeking and event.button:
            self._seek_from_x(event.x)

    def on_mouse_up(self, _: events.MouseUp) -> None:
        if self._seeking:
            self._seeking = False
            self.release_mouse()

    def _seek_from_x(self, x: int) -> None:
        if self.duration <= 0:
            return
        bar_w = getattr(self, "_bar_w", self.content_size.width)
        ratio = max(0.0, min(1.0, x / max(1, bar_w - 1)))
        self.post_message(TransportBar.SeekTo(ratio * self.duration))


class TransportBar(Widget):
    """Playback transport bar with album art when playing.

    Self-wiring: subscribes to QobitApp reactives on mount so any instance
    placed anywhere in the widget tree is always live."""

    class SeekTo(Message):
        def __init__(self, position: float) -> None:
            super().__init__()
            self.position = position

    is_paused: reactive[bool] = reactive(False)

    DEFAULT_CSS = """
    TransportBar {
        height: 6;
        border: round $primary-lighten-2;
        /* top-right: audio resolution of the current track */
        border-title-color: $accent;
        border-title-align: right;
        border-title-style: bold;
        /* bottom-left: play/pause status */
        border-subtitle-color: $primary-lighten-2;
        border-subtitle-align: left;
        layout: horizontal;
        padding: 0 1;
    }
    TransportBar.-playing {
        border: round $accent;
        border-subtitle-color: $accent;
    }
    TransportBar:hover {
        background: $boost;
    }
    TransportBar TGPImage {
        width: 8;
        height: 4;
        margin-right: 1;
        display: none;
    }
    TransportBar.-playing TGPImage {
        display: block;
    }
    """

    def compose(self) -> ComposeResult:
        yield TGPImage(id="tb-art")
        yield _TransportContent()

    def on_mount(self) -> None:
        cell = get_cell_size()
        if cell.width > 0 and cell.height > 0:
            img_w = round(4 * cell.height / cell.width)
            self.query_one(TGPImage).styles.width = img_w
        app: QobitApp = self.app  # type: ignore[assignment]
        self.watch(app, "now_playing", self._on_now_playing, init=True)
        self.watch(app, "is_playing", self._on_is_playing, init=True)
        self.watch(app, "is_paused", self._on_is_paused, init=True)
        self.watch(app, "playback_pos", self._on_pos, init=True)
        self.watch(app, "playback_dur", self._on_dur, init=True)
        self.watch(app, "status_msg", self._on_status_msg, init=True)
        self.watch(app, "quality_label", self._on_quality, init=True)

    def _status(self) -> str:
        app: QobitApp = self.app  # type: ignore[assignment]
        if not app.now_playing:
            return ""
        return "⏸  Now Playing" if app.is_paused else "▶  Now Playing"

    def _on_now_playing(self, track: object) -> None:
        content = self.query_one(_TransportContent)
        if track:
            content.label = f"{track.artist} — {track.display_title}"  # type: ignore[union-attr]
            content.album = track.album  # type: ignore[union-attr]
            if track.image_url:  # type: ignore[union-attr]
                self._fetch_art(track.image_url)  # type: ignore[union-attr]
        else:
            content.label = ""
            content.album = ""
        self.border_subtitle = self._status()

    def _on_is_playing(self, playing: bool) -> None:
        self.set_class(playing, "-playing")

    def _on_is_paused(self, paused: bool) -> None:
        self.is_paused = paused
        self.border_subtitle = self._status()

    def _on_quality(self, label: str) -> None:
        # Audio resolution on the top-right of the border (no content space used).
        self.border_title = label

    def _on_pos(self, pos: float) -> None:
        self.query_one(_TransportContent).position = pos

    def _on_dur(self, dur: float) -> None:
        self.query_one(_TransportContent).duration = dur

    def _on_status_msg(self, msg: str) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        content = self.query_one(_TransportContent)
        if msg:
            content.label = msg
        elif app.now_playing:
            content.label = f"{app.now_playing.artist} — {app.now_playing.display_title}"
        else:
            content.label = ""

    @work
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_mounted:
            try:
                self.query_one(TGPImage).image = img
            except NoMatches:
                pass
