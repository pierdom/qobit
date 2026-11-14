"""Play contexts — who is allowed to change ``QobitApp``'s timeline.

``QobitApp._timeline`` + ``_cursor`` is the one play context every widget
reads (Queue tab, NowPlayingHero, TransportBar). *Writing* it goes through a
context object, so a second owner can exist without touching the readers:

- ``LocalPlayContext`` — qobit owns the queue (the behaviour that predates
  Connect, moved here verbatim from app.py).
- ``ConnectPlayContext`` — while qobit is the **active Qobuz Connect
  renderer**, the server owns the queue: the timeline is a mirror rebuilt from
  server queue events, and playback is driven by ``connect.ConnectRenderer``.
  Phase 1 is mirror-only, so local queue *edits* are refused with a notice; in
  phase 2 those methods become ``CTRL_SRVR_QUEUE_*`` commands (see
  docs/qobuz-connect-design.md). Every timeline mutator must exist on both
  contexts — that's what keeps phase 2 a drop-in.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..connect import device_info
from ..connect.queue import ServerQueue
from ..connect.renderer import ConnectRenderer
from ..connect.session import ConnectSession
from ..qobuz.client import QobuzError
from ..qobuz.models import Track

if TYPE_CHECKING:
    from .app import QobitApp


class LocalPlayContext:
    """qobit owns the timeline. Logic unchanged from the pre-Connect app.py."""

    def __init__(self, app: QobitApp) -> None:
        self.app = app

    async def load(self, track: Track, queue: list[Track] | None, start: float) -> None:
        app = self.app
        # Resume-in-place: a bare replay of the current track (e.g. session
        # restore) shouldn't spawn a duplicate context entry.
        if queue is None and app._cursor >= 0 and app._timeline[app._cursor].id == track.id:
            await app._do_play(track, start=start)
            return
        # Launch a new context: keep the past (history + the interrupted track),
        # then splice in the new track and its queue. The old Up Next is dropped
        # — this is a deliberate new selection, not a skip within the queue.
        past = app._timeline[: app._cursor + 1] if app._cursor >= 0 else []
        app._timeline = [*past, track, *(queue or [])]
        app._cursor = len(past)
        app._trim_history()
        app.queue_version += 1
        await app._do_play(track, start=start)

    async def jump_to(self, index: int) -> None:
        app = self.app
        if not 0 <= index < len(app._timeline):
            return
        app._cursor = index
        app.queue_version += 1
        await app._do_play(app._timeline[index])

    async def advance(self) -> None:
        app = self.app
        at_end = app._cursor + 1 >= len(app._timeline)
        if at_end and app.radio_mode and app.now_playing is not None:
            # Timeline ran out under endless radio: refill from suggestions
            # seeded by the just-finished track (still now_playing here).
            suggestions = await app._fetch_radio(app.now_playing)
            if suggestions:
                app._timeline.extend(suggestions)
                at_end = False
        if at_end:
            return
        app._cursor += 1
        app._trim_history()
        app.queue_version += 1
        await app._do_play(app._timeline[app._cursor])

    def clear_queue(self) -> bool:
        app = self.app
        if app._play_queue:
            del app._timeline[app._cursor + 1 :]
            app.queue_version += 1
        return True

    def clear_history(self) -> bool:
        app = self.app
        if app._history:
            del app._timeline[: app._cursor]
            app._cursor = 0
            app.queue_version += 1
        return True

    def queue_next(self, track: Track) -> bool:
        app = self.app
        app._timeline.insert(app._cursor + 1, track)
        app.queue_version += 1
        return True

    def queue_last(self, track: Track) -> bool:
        app = self.app
        app._timeline.append(track)
        app.queue_version += 1
        return True

    def remove(self, track: Track) -> bool:
        """Drop a track from Up Next. Identity first (the exact queued
        instance), then the first id match ahead of the cursor."""
        app = self.app
        for i in range(app._cursor + 1, len(app._timeline)):
            if app._timeline[i] is track:
                del app._timeline[i]
                app.queue_version += 1
                return True
        for i in range(app._cursor + 1, len(app._timeline)):
            if app._timeline[i].id == track.id:
                del app._timeline[i]
                app.queue_version += 1
                return True
        return False

    def replace_upcoming(self, tracks: list[Track]) -> bool:
        """Swap Up Next for ``tracks`` (song radio), keeping the current track."""
        app = self.app
        if app._cursor >= 0:
            del app._timeline[app._cursor + 1 :]
            app._timeline.extend(tracks)
        else:
            app._timeline = list(tracks)
            app._cursor = -1
        app.queue_version += 1
        return True


_REFUSED = "Queue is controlled by Qobuz Connect — edit it from the Qobuz app"


class ConnectPlayContext:
    """qobit is (or may become) the active Connect renderer.

    Implements the PlayContext methods (used while active) *and* the
    renderer's ``PlayerPort`` (how the renderer drives the real player).
    """

    def __init__(self, app: QobitApp) -> None:
        self.app = app
        self.session = ConnectSession(
            mint=app._client.create_connect_token,
            device=device_info(),
            on_message=self._on_message,
            on_status=self._on_status,
        )
        self.renderer = ConnectRenderer(self, self.session.send)
        # Aligned 1:1 with app._timeline while active: queue_item_id per row.
        self._item_ids: list[int] = []
        self._tracks: dict[int, Track] = {}
        self._fetch_sem = asyncio.Semaphore(8)
        self._mirror_gen = 0

    @property
    def active(self) -> bool:
        return self.renderer.active

    async def _on_message(self, m) -> None:  # noqa: ANN001 — QConnectMessage
        await self.renderer.handle(m)

    def _on_status(self, status: str) -> None:
        if status == "offline" and self.renderer.active:
            # Socket gone while we were active: the server will re-register us
            # inactive. Fall back to local playback with the mirror kept.
            self.renderer.active = False
            self.active_changed(False)
        self.app.connect_state = "active" if self.renderer.active else status

    # ── PlayContext (used while active) ───────────────────────────────────────

    def _refuse(self) -> bool:
        self.app._flash_status(_REFUSED)
        return False

    async def load(self, track: Track, queue: list[Track] | None, start: float) -> None:
        self._refuse()  # phase 2: CTRL_SRVR_QUEUE_LOAD_TRACKS

    async def jump_to(self, index: int) -> None:
        if 0 <= index < len(self._item_ids):
            await self.renderer.jump(self._item_ids[index])

    async def advance(self) -> None:
        await self.renderer.track_ended()

    def clear_queue(self) -> bool:
        return self._refuse()  # phase 2: QUEUE_REMOVE_TRACKS(upcoming)

    def clear_history(self) -> bool:
        return self._refuse()  # phase 2: QUEUE_REMOVE_TRACKS(past)

    def queue_next(self, track: Track) -> bool:
        return self._refuse()  # phase 2: QUEUE_INSERT_TRACKS(after current)

    def queue_last(self, track: Track) -> bool:
        return self._refuse()  # phase 2: QUEUE_ADD_TRACKS

    def remove(self, track: Track) -> bool:
        return self._refuse()  # phase 2: QUEUE_REMOVE_TRACKS([item_id])

    def replace_upcoming(self, tracks: list[Track]) -> bool:
        return self._refuse()  # phase 2/3: autoplay or QUEUE_ADD_TRACKS

    # ── PlayerPort (driven by ConnectRenderer) ────────────────────────────────

    async def load_track(self, track_id: int, start_s: float, play: bool) -> bool:
        """Make ``track_id`` current. Returns False when it can't be played
        (unavailable, preview-only) so the renderer skips it."""
        app = self.app
        track = await self._track(track_id)
        if track is None:
            app._flash_status("Qobuz Connect: track unavailable")
            return False
        if not play:
            # Prepared, not started: shown paused at start_s; audio starts on PLAY.
            if app._player.running:
                app._player.stop()
            app.now_playing = track
            app.playback_pos = start_s
            app.playback_dur = float(track.duration or 0)
            app.is_playing = False
            app.is_paused = True
            return True
        result = await app._play_stream(track, start_s)
        if result == "ok" and not app.playback_dur:
            # mpv's duration only arrives with the next poll; the apps' progress
            # bars need it in the very first report.
            app.playback_dur = float(track.duration or 0)
        if result == "ok" and app._last_stream is not None:
            st = app._last_stream
            self.renderer.report_quality(st.format_id, round(st.sampling_rate), st.bit_depth or 16)
        if result == "sample":
            app._flash_status(f"Not streamable (preview only): {track.display_title}")
        return result == "ok"

    async def _track(self, track_id: int) -> Track | None:
        if track_id in self._tracks:
            return self._tracks[track_id]
        async with self._fetch_sem:
            if track_id in self._tracks:
                return self._tracks[track_id]
            try:
                data = await self.app._client.get_track(str(track_id))
            except QobuzError:
                return None
            track = Track.from_api(data)
            self._tracks[track_id] = track
            return track

    def set_paused(self, paused: bool) -> None:
        self.app._player.set_paused(paused)
        self.app.is_paused = paused

    def seek(self, pos_s: float) -> None:
        self.app._player.seek_to(pos_s)

    def stop(self) -> None:
        self.app._player.stop()

    def is_running(self) -> bool:
        return self.app._player.running

    def position_s(self) -> float:
        return float(self.app.playback_pos)

    def duration_s(self) -> float:
        return float(self.app.playback_dur)

    async def queue_changed(self, queue: ServerQueue, current_item_id: int) -> None:
        """Rebuild the mirror (app._timeline/_cursor) from the server queue.
        Hydrates track ids to Tracks first so the Queue tab never shows half a
        queue; a newer rebuild started meanwhile wins (generation guard)."""
        self._mirror_gen += 1
        gen = self._mirror_gen
        order = queue.play_order()
        tracks = await asyncio.gather(*(self._track(it.track_id) for it in order))
        if gen != self._mirror_gen or not self.renderer.active:
            return
        rows = [(it.item_id, t) for it, t in zip(order, tracks) if t is not None]
        self._item_ids = [i for i, _ in rows]
        app = self.app
        app._timeline = [t for _, t in rows]
        app._cursor = (
            self._item_ids.index(current_item_id) if current_item_id in self._item_ids else -1
        )
        app.queue_version += 1

    def active_changed(self, active: bool) -> None:
        app = self.app
        app.connect_state = "active" if active else "ready"
        if active:
            app._pending_resume = None
            app._flash_status("Playing via Qobuz Connect")
            return
        # Handed off (or disconnected): keep the mirrored timeline as the new
        # local one, paused where it was, so space resumes locally.
        if app.now_playing is not None:
            app._pending_resume = (app.now_playing, float(app.playback_pos))
            app.is_playing = False
            app.is_paused = True
        self._item_ids = []
        app._flash_status("Qobuz Connect: playback moved to another device")
