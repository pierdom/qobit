from __future__ import annotations

import contextlib
from functools import lru_cache
from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.color import Color
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage

from ...qobuz.models import quality_text
from .._images import fetch_image
from ..themes import BAR_END, BAR_START, HIRES, theme_color

if TYPE_CHECKING:
    from ..app import QobitApp


def _fmt(secs: float) -> str:
    h, r = divmod(int(max(0.0, secs)), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# Eighths of a cell: the bar's leading edge moves a fraction of a cell at a
# time instead of jumping a whole one.
_EIGHTHS = " ▏▎▍▌▋▊▉"


@lru_cache(maxsize=16)
def _gradient(start: str, end: str, width: int) -> tuple[str, ...]:
    """One colour per cell, ``start`` → ``end`` across ``width`` cells."""
    a, b = Color.parse(start), Color.parse(end)
    if width <= 1 or a == b:
        return (a.hex,) * max(1, width)
    return tuple(a.blend(b, i / (width - 1)).hex for i in range(width))


def _progress_bar(app: object, width: int, position: float, duration: float) -> Text:
    """The progress bar: filled cells in the theme's bar colours (a gradient
    when ``qobit-bar-start`` and ``-end`` differ, as in cyberdeck), the edge
    to an eighth of a cell, the rest a dim track. Rich Text doesn't resolve
    Textual's $variables, so the colours are the theme's actual values."""
    accent = theme_color(app, "accent")
    colours = _gradient(theme_color(app, BAR_START), theme_color(app, BAR_END), width)
    fill = width * min(position, duration) / duration if duration > 0 else 0.0
    full, eighth = int(fill), int((fill - int(fill)) * 8)
    bar = Text(no_wrap=True)
    for i in range(full):
        bar.append("█", style=colours[i])
    if eighth and full < width:
        bar.append(_EIGHTHS[eighth], style=colours[full])
        full += 1
    bar.append("░" * (width - full), style=f"dim {accent}".strip())
    return bar


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
                "Nothing playing  ·  Enter on any track plays it",
                style="dim",
                no_wrap=True,
            )

        line2 = Text(self.album, no_wrap=True, overflow="ellipsis", style="dim")

        time_str = f" {_fmt(self.position)} / {_fmt(self.duration)}"
        bar_w = max(1, w - len(time_str))
        self._bar_w = bar_w

        bar = _progress_bar(self.app, bar_w, self.position, self.duration)
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


def is_hires(bit_depth: int | None, sampling_rate_hz: float) -> bool:
    """Above CD quality: more than 16 bits or more than 48 kHz."""
    return (bit_depth or 16) > 16 or sampling_rate_hz > 48000


def _lamp(app: QobitApp) -> tuple[str, str] | None:
    """The bit-perfect lamp, or None while unchecked. Software volume below
    100% is DSP: then the lamp says so, whatever mpv reports."""
    colour = lambda name: f"bold {theme_color(app, name)}"  # noqa: E731
    if app.software_volume_enabled and app.volume < 100:
        return (f"◌ SOFTWARE VOLUME {app.volume}%", colour("warning"))
    signal = app.signal
    if signal is None or signal.state == "unknown":
        return None
    if signal.state == "ok":
        return ("◉ BIT-PERFECT", colour("success"))
    return (f"◌ {signal.state.upper()} {signal.detail}".rstrip(), colour("error"))


def signal_parts(app: QobitApp) -> tuple[Content | None, Content | None]:
    """(lamp, ``HI-RES 24-BIT · 96 kHz``) for the playing stream, either None.
    Colours are the theme's actual values (border titles don't resolve
    $variables), so watchers redraw them on a theme change."""
    stream = app._last_stream if app.now_playing else None
    if stream is None:
        return None, None
    lamp = _lamp(app)
    quality: list[tuple[str, str] | str] = []
    if is_hires(stream.bit_depth, stream.sampling_rate):
        quality.append(("HI-RES ", f"bold {theme_color(app, HIRES, 'warning')}"))
    quality.append(quality_text(stream.bit_depth or 16, stream.sampling_rate / 1000))
    return (Content.assemble(lamp) if lamp else None), Content.assemble(*quality)


def signal_readout(app: QobitApp) -> Content | str:
    """The transport's bottom-right readout, ``[ ◉ BIT-PERFECT  ·  HI-RES
    24-BIT · 96 kHz ]`` ("" when nothing plays)."""
    lamp, quality = signal_parts(app)
    if quality is None:
        return ""
    middle = Content.assemble(lamp, "  ·  ", quality) if lamp else quality
    return Content.assemble("[ ", middle, " ]")


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
        /* Same dim-accent → full-accent ladder every other panel uses on
           focus, here driven by playback state instead since the transport
           bar is never keyboard-focused. Round like every other panel. */
        border: round $accent 40%;
        /* top-left: play/pause status ("Now Playing") */
        border-title-color: $accent 40%;
        border-title-align: left;
        border-title-style: bold;
        /* bottom-right: audio resolution of the current track */
        border-subtitle-color: $accent;
        border-subtitle-align: right;
        layout: horizontal;
        padding: 0 1;
    }
    TransportBar.-playing {
        border: round $accent;
        border-title-color: $accent;
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
    /* Art shows whenever a track is loaded, also paused or restored after
       a restart (is_playing is still False until the first play). */
    TransportBar.-has-track TGPImage {
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
        self.watch(app, "radio_mode", self._on_radio_mode, init=True)
        self.watch(app, "connect_state", self._on_radio_mode, init=True)
        self.watch(app, "volume", self._on_volume, init=True)
        self.watch(app, "software_volume_enabled", self._on_volume, init=True)
        self.watch(app, "signal", self._on_volume, init=True)
        self.watch(app, "theme", self._on_theme, init=False)

    def _status(self) -> str:
        # Bracket-readout style — the one piece of chrome that's always on
        # screen reads like a console status line, not a sentence.
        app: QobitApp = self.app  # type: ignore[assignment]
        tags = []
        # While Connect-active the queue is the server's, so qobit's own radio
        # refill is dormant — don't advertise it.
        if app.radio_mode and app.connect_state != "active":
            tags.append("[ ◈ RADIO ]")
        connect = {
            "active": "[ ⇄ CONNECT · ACTIVE ]",
            "ready": "[ ⇄ CONNECT ]",
            "connecting": "[ ⇄ CONNECT … ]",
            "offline": "[ ⇄ CONNECT · OFFLINE ]",
        }.get(app.connect_state)
        if connect:
            tags.append(connect)
        if app.now_playing:
            tags.insert(0, "[ ⏸ PAUSED ]" if app.is_paused else "[ ▶ PLAYING ]")
        return "  ·  ".join(tags)

    def _on_radio_mode(self, _: bool) -> None:
        self.border_title = self._status()

    def _on_now_playing(self, track: object) -> None:
        content = self.query_one(_TransportContent)
        self.set_class(bool(track), "-has-track")
        if track:
            content.label = f"{track.artist} — {track.display_title}"  # type: ignore[union-attr]
            content.album = track.album  # type: ignore[union-attr]
            if track.image_url:  # type: ignore[union-attr]
                self._fetch_art(track.image_url)  # type: ignore[union-attr]
        else:
            content.label = ""
            content.album = ""
        self.border_title = self._status()

    def _on_is_playing(self, playing: bool) -> None:
        self.set_class(playing, "-playing")

    def _on_is_paused(self, paused: bool) -> None:
        self.is_paused = paused
        self.border_title = self._status()

    _quality_label: str = ""

    def _on_quality(self, label: str) -> None:
        # Audio resolution on the bottom-right of the border (no content space used).
        self._quality_label = label
        self._update_subtitle()

    def _on_volume(self, _: object) -> None:
        self._update_subtitle()

    def _on_theme(self, _: object) -> None:
        self._update_subtitle()
        self.query_one(_TransportContent).refresh()

    def _update_subtitle(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        self.border_subtitle = signal_readout(app) if self._quality_label else ""

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

    @work(exclusive=True, group="_fetch_art")  # a newer request cancels a stale one
    async def _fetch_art(self, url: str) -> None:
        img = await fetch_image(url)
        if img is not None and self.is_attached:
            with contextlib.suppress(NoMatches):
                self.query_one(TGPImage).image = img
