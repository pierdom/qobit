"""What the app does about lyrics: look them up for the playing track, keep the
current line, prefetch the next track's, retry a failed lookup, remember the
reader's timing and "wrong lyrics". The app keeps the reactives the views
watch (``lyrics_status``, ``lyrics_line``, ``lyrics_version``) and the actions
and bindings; this holds the rest.

A new now-playing track starts a lookup (disk cache, then LRCLIB: see
``qobit/lyrics``). The current line is recomputed on each position tick and
``lyrics_line`` changes only when the line does, so the views redraw once per
line, not once per tick. The next track's lyrics are fetched near the end of
this one.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any, Protocol

from textual.timer import Timer

from ..lyrics import Lyrics, LyricsService, Query
from ..qobuz.models import Track

# The next track's lyrics are fetched this many seconds before the end, so they
# are there when it starts.
PREFETCH_SECS = 45.0
# A lookup that failed (a network blip) is asked again after this, a few times.
RETRY_SECS = 20.0
RETRIES = 3
# The poll reads the position every 0.5 s: the current lyric line is chosen
# this far ahead, so it is never late by the poll's interval.
LEAD = 0.3
# What a nudge of the lyrics' timing (`,` and `.`) moves them by.
STEP = 0.5


class LyricsHost(Protocol):
    """What the controller reads from, and tells, the app."""

    now_playing: Track | None
    playback_pos: float
    playback_dur: float
    is_playing: bool
    is_paused: bool
    is_attached: bool
    is_running: bool
    lyrics_enabled: bool
    lyrics_status: str
    lyrics_line: int
    lyrics_version: int

    def run_worker(
        self, work: Callable[[], Coroutine[Any, Any, Any]], *, group: str, exclusive: bool
    ) -> Any:
        """Textual's: a callable, so a worker cancelled before it starts never
        leaves an un-awaited coroutine behind."""

    def set_timer(self, delay: float, callback: Callable[[], None]) -> Timer: ...


def query_for(track: Track) -> Query:
    return Query(
        title=track.title,
        artist=track.artist,
        album=track.album,
        duration=track.duration,
        isrc=track.isrc,
        version=track.version or "",
    )


class LyricsController:
    def __init__(
        self,
        host: LyricsHost,
        *,
        next_track: Callable[[], Track | None],
        flash: Callable[[str], None],
        service: LyricsService | None = None,
    ) -> None:
        """``next_track``: the track after the playing one, or None (also while
        Qobuz Connect drives: the server decides what plays next). ``flash``
        shows a transient notice."""
        self._host = host
        self._next_track = next_track
        self._flash = flash
        self.service = service or LyricsService.default()
        self.lyrics: Lyrics | None = None
        self.offset = 0.0  # the reader's sync nudge, seconds
        self.query: Query | None = None  # of the playing track's lookup
        self.prefetched: str | None = None  # id of the next track already asked for
        self._retries = 0
        self._prefetch_retried: set[str] = set()
        self._retry_timer: Timer | None = None

    async def aclose(self) -> None:
        await self.service.aclose()

    # ── the playing track ────────────────────────────────────────────────────

    def track_changed(self, track: Track | None) -> None:
        """Forget the last track's lyrics and, if lyrics are on, look this one up."""
        host = self._host
        self.lyrics = None
        self.offset = 0.0
        self.query = None
        self.prefetched = None
        self._retries = 0
        self._prefetch_retried = set()
        if self._retry_timer is not None:
            self._retry_timer.stop()  # it belonged to the last lookup
            self._retry_timer = None
        host.lyrics_line = -1
        if track is None:
            host.lyrics_status = "idle"
        elif not host.lyrics_enabled:
            host.lyrics_status = "off"
        elif not host.is_running:
            host.lyrics_status = "idle"  # nothing to run a lookup on (yet)
        else:
            self.query = query_for(track)
            host.lyrics_status = "loading"
            self._look_up(track)
        host.lyrics_version += 1

    def _look_up(self, track: Track) -> None:
        # exclusive: a newer track cancels a stale lookup
        self._host.run_worker(partial(self._load, track), group="_load_lyrics", exclusive=True)

    async def _load(self, track: Track) -> None:
        host = self._host
        result = await self.service.lookup(query_for(track))
        now = host.now_playing
        if not host.is_attached or now is None or now.id != track.id or not host.lyrics_enabled:
            return
        self.lyrics = result.lyrics
        self.offset = result.offset
        host.lyrics_status = result.status
        self.update_line()
        host.lyrics_version += 1
        if result.status == "failed" and self._retries < RETRIES:
            self._retries += 1
            self._retry_timer = host.set_timer(RETRY_SECS, lambda: self._retry(track))

    def _retry(self, track: Track) -> None:
        """Ask again for the playing track whose lookup failed."""
        host = self._host
        now = host.now_playing
        if (
            now is None
            or now.id != track.id
            or not host.lyrics_enabled
            or host.lyrics_status != "failed"
        ):
            return  # another track, or the reader turned them off, or it worked
        host.lyrics_status = "loading"
        host.lyrics_version += 1
        self._look_up(track)

    def update_line(self) -> None:
        """Move ``lyrics_line`` to the line at the playback position."""
        ly = self.lyrics
        if ly is None or not ly.synced:
            return
        host = self._host
        host.lyrics_line = ly.line_at(host.playback_pos + LEAD - self.offset)

    # ── the next track ───────────────────────────────────────────────────────

    def maybe_prefetch(self) -> None:
        host = self._host
        if (
            not host.lyrics_enabled
            or not host.is_playing
            or host.is_paused
            or host.playback_dur <= 0
            or host.playback_dur - host.playback_pos > PREFETCH_SECS
        ):
            return
        nxt = self._next_track()
        if nxt is None or nxt.id == self.prefetched:
            return
        self.prefetched = nxt.id
        self._prefetch(nxt)

    def _prefetch(self, track: Track) -> None:
        self._host.run_worker(
            partial(self._prefetch_load, track), group="_prefetch_lyrics", exclusive=False
        )

    async def _prefetch_load(self, track: Track) -> None:
        result = await self.service.lookup(query_for(track))  # fills the cache
        if result.status == "failed" and track.id not in self._prefetch_retried:
            self._prefetch_retried.add(track.id)  # once: the position tick asks again
            self._host.set_timer(RETRY_SECS, lambda: self._forget_prefetch(track.id))

    def _forget_prefetch(self, track_id: str) -> None:
        if self.prefetched == track_id:
            self.prefetched = None

    # ── the reader's corrections ─────────────────────────────────────────────

    def nudge(self, delta: float) -> None:
        """Shift the lyrics' timing for this track (remembered with them)."""
        query = self.query
        if query is None or self.lyrics is None or not self.lyrics.synced:
            return
        host = self._host
        self.offset = round(self.offset + delta, 3)
        host.run_worker(
            partial(self._save_offset, query, self.offset),
            group="_save_lyrics_offset",
            exclusive=True,
        )
        self.update_line()
        host.lyrics_version += 1  # the subtitle shows the offset
        self._flash(f"Lyrics {self.offset:+.1f} s")

    async def _save_offset(self, query: Query, offset: float) -> None:
        if query is self.query:  # quick presses: the latest one is what is saved
            offset = self.offset
        await self.service.set_offset(query, offset)

    def reject(self) -> None:
        """These lyrics are wrong for this track: gone, and not asked for again."""
        query = self.query
        host = self._host
        if query is None or host.lyrics_status != "found":
            return
        host.run_worker(
            partial(self.service.reject, query), group="_reject_lyrics", exclusive=False
        )
        self.lyrics = None
        host.lyrics_line = -1
        host.lyrics_status = "none"
        host.lyrics_version += 1
        self._flash("Lyrics removed for this track")
