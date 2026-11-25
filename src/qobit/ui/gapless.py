"""Gapless playback: the next track queued in mpv.

Late in a track the next one's stream is resolved and appended to mpv's
playlist: mpv has opened it by the boundary and, with the same format, keeps
the device open, so nothing is heard between them. The natural end of the last
track is unchanged (nothing appended: mpv exits, the poll advances, endless
radio refills). Local playback only: under Qobuz Connect the server owns the
queue and track ends.

``GaplessQueue`` holds the state (what is queued, a prefetch under way, a track
that wasn't playable) and the four things that happen to it: late in the track
(``maybe_prefetch``), the queue changing (``check``, ``drop``), mpv moving on by
itself (``on_transition``) and a new mpv (``reset``). The app tells it about
the playback state through ``GaplessHost`` and keeps what only it can do:
moving the cursor and starting a track in the transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from functools import partial
from typing import Any, Protocol

from ..audio.player import MpvPlayer
from ..qobuz.client import QobuzError
from ..qobuz.models import NoStreamError, StreamUrl, Track
from ..qobuz.streams import StreamResolver

# The next track is queued in mpv this many seconds before the current one
# ends: a stream URL lasts ~10 minutes, so not at the start.
PREFETCH_SECS = 30.0


class GaplessHost(Protocol):
    """What the queue reads from, and asks of, the app."""

    gapless: bool  # the user's setting
    now_playing: Track | None
    is_playing: bool
    is_paused: bool
    playback_pos: float
    playback_dur: float
    radio_mode: bool
    is_local: bool  # qobit owns the timeline (not Qobuz Connect)
    advance_busy: bool  # an advance is already starting the next track

    def next_track(self) -> Track | None:
        """The track after the playing one."""

    def extend_timeline(self, tracks: list[Track]) -> None:
        """Add tracks at the end of the timeline (radio refill)."""

    def step_to_next(self, track: Track, stream: StreamUrl) -> None:
        """mpv is playing ``track``, the one after the old cursor: move the
        cursor onto it and show it in the transport (nothing to start)."""

    def run_worker(
        self,
        work: Callable[[], Coroutine[Any, Any, Any]] | Callable[[], Any],
        *,
        group: str,
        exclusive: bool = False,
        thread: bool = False,
    ) -> Any: ...


class GaplessQueue:
    def __init__(
        self,
        host: GaplessHost,
        *,
        player: Callable[[], MpvPlayer],
        streams: StreamResolver,
        fetch_radio: Callable[[Track], Awaitable[list[Track]]],
        advance: Callable[[], None],
    ) -> None:
        """``player``: the running mpv (asked each time: the app may swap it).
        ``advance``: start the next track the ordinary way."""
        self._host = host
        self._player = player
        self._streams = streams
        self._fetch_radio = fetch_radio
        self._advance = advance
        # The track queued in mpv behind the playing one: (track id, its stream).
        self.next_up: tuple[str, StreamUrl] | None = None
        # A track whose stream wasn't playable: the natural end plays (or
        # skips, with the notice) it the ordinary way.
        self.failed: str | None = None
        self._prefetching = False

    def reset(self) -> None:
        """A new mpv: nothing is queued behind its track."""
        self.next_up = None
        self.failed = None

    # ── late in a track ──────────────────────────────────────────────────────

    def maybe_prefetch(self) -> None:
        """On each position tick: line up the next track when the end is near."""
        host = self._host
        if (
            not host.gapless
            or self.next_up is not None
            or self._prefetching
            or not host.is_local
            or not host.is_playing
            or host.is_paused  # paused, the URL could expire before it's heard
            or host.playback_dur <= 0
            or host.playback_dur - host.playback_pos > PREFETCH_SECS
            or not self._player().running
        ):
            return
        self._prefetching = True
        host.run_worker(self._prefetch, group="_prefetch")

    async def _prefetch(self) -> None:
        try:
            await self._prefetch_next()
        finally:
            self._prefetching = False

    async def _prefetch_next(self) -> None:
        host = self._host
        gen, _ = self._player().snapshot()
        playing = host.now_playing
        track = host.next_track()
        if track is None and host.radio_mode and playing is not None:
            # Out of queue under endless radio: refill now, as advance() would
            # at the end of the track.
            suggestions = await self._fetch_radio(playing)
            if suggestions and host.next_track() is None and host.now_playing is playing:
                host.extend_timeline(suggestions)
            track = host.next_track()
        if track is None or track.id == self.failed:
            return
        try:
            stream = await self._streams.resolve(str(track.id))
        except (QobuzError, NoStreamError):
            stream = None
        if stream is None or stream.sample:
            # Not ours to report: the natural end plays (or skips, with the
            # notice) this track the ordinary way.
            self.failed = track.id
            return
        now = host.next_track()  # the queue may have changed while resolving
        if self._player().snapshot() != (gen, True) or now is None or now.id != track.id:
            return
        if await asyncio.to_thread(self._player().append, stream.url, gen):
            self.next_up = (track.id, stream)

    # ── the queue changed ────────────────────────────────────────────────────

    def drop(self) -> None:
        """Forget the track queued in mpv, and take it out of mpv."""
        if self.next_up is None:
            return
        self.next_up = None
        gen, running = self._player().snapshot()
        if running:
            self._host.run_worker(
                partial(self._player().clear_next, gen), group="_clear_next", thread=True
            )

    def check(self) -> None:
        """The queue changed: the track queued in mpv must still be the next."""
        if self.next_up is None:
            return
        nxt = self._host.next_track()
        if nxt is None or nxt.id != self.next_up[0]:
            self.drop()

    # ── mpv moved on by itself ───────────────────────────────────────────────

    def on_transition(self) -> None:
        """mpv went on to the track queued behind the last one: move the cursor
        and the transport onto it (nothing to start)."""
        host = self._host
        info, self.next_up = self.next_up, None
        nxt = host.next_track()
        if not host.is_local or host.advance_busy:
            return  # Connect took over, or an advance in flight starts its own
        if info is None or nxt is None or nxt.id != info[0]:
            # Not what the queue says any more (edited just as mpv moved on):
            # play the right track the ordinary way.
            self._advance()
            return
        host.step_to_next(nxt, info[1])
