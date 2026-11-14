"""ConnectRenderer — qobit's behaviour as a Qobuz Connect renderer.

Split of authority (docs/qobuz-connect-design.md):

- **The server owns the queue.** ``ServerQueue`` mirrors it; this class never
  edits it (phase 1 = mirror only).
- **The renderer owns playback.** Commands from the server, and local transport
  keys, act on the player immediately and are *reported* back with
  ``RNDR_SRVR_STATE_UPDATED``. Natural end of track is the renderer's job too.

Protocol facts this relies on, all verified against the iOS app (see the
"Verified end-to-end" table in docs/qobuz-connect.md):

- ``SET_STATE`` is a delta. ``current_track`` always means *jump to it* —
  queue item ids restart at 0 on every new queue, so never compare ids to
  decide whether to reload.
- Picking a song in an app is only ``QUEUE_TRACKS_LOADED``; no ``SET_STATE``
  follows. The active renderer must start ``tracks[queue_position]`` itself.

UI-agnostic: it drives a ``PlayerPort`` (implemented by the TUI, or a fake in
tests) and sends through a ``send`` callable.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Protocol

from .proto import qconnect_pb2 as qp
from .queue import LOOP_OFF, QueueItem, ServerQueue
from .wire import now_ms

log = logging.getLogger(__name__)

PLAYING = qp.PLAYING_STATE_PLAYING
PAUSED = qp.PLAYING_STATE_PAUSED
STOPPED = qp.PLAYING_STATE_STOPPED

# Queue events that change structure. Rather than replaying each delta, the
# mirror asks for a full QUEUE_STATE snapshot (see queue.py).
_RESYNC_ON = {
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_INSERTED,
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_ADDED,
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_REMOVED,
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_REORDERED,
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_CLEARED,
    qp.MESSAGE_TYPE_SRVR_CTRL_SHUFFLE_MODE_SET,
    qp.MESSAGE_TYPE_SRVR_CTRL_AUTOPLAY_MODE_SET,
    qp.MESSAGE_TYPE_SRVR_CTRL_AUTOPLAY_TRACKS_LOADED,
    qp.MESSAGE_TYPE_SRVR_CTRL_AUTOPLAY_TRACKS_REMOVED,
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_ADDED_FROM_AUTOPLAY,
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_ERROR_MESSAGE,
    qp.MESSAGE_TYPE_SRVR_RNDR_SET_SHUFFLE_MODE,
}


class PlayerPort(Protocol):
    """What the renderer needs from the actual player."""

    async def load_track(self, track_id: int, start_s: float, play: bool) -> bool:
        """Make ``track_id`` current at ``start_s``. ``play=False`` prepares it
        (shown as paused) without starting audio. Returns False if the track
        can't be played (unavailable / preview-only) — the renderer skips it."""
        ...

    def set_paused(self, paused: bool) -> None: ...
    def seek(self, pos_s: float) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
    def position_s(self) -> float: ...
    def duration_s(self) -> float: ...
    async def queue_changed(self, queue: ServerQueue, current_item_id: int) -> None: ...
    def active_changed(self, active: bool) -> None: ...


class ConnectRenderer:
    def __init__(self, port: PlayerPort, send: Callable[..., None]) -> None:
        self.port = port
        self._send = send
        self.queue = ServerQueue()
        self.active = False
        self.state = STOPPED
        self.item_id = -1
        self.track_id = 0
        # SET_STATE's next_track hint — used only when the mirror can't answer
        # (e.g. before the first QUEUE_STATE arrives).
        self._hint: QueueItem | None = None
        self.loop = LOOP_OFF
        self._pos_s = 0.0  # position while not running (paused/prepared)
        self._loaded = False  # player holds the current item (mpv started for it)

    # ── server → renderer ─────────────────────────────────────────────────────

    async def handle(self, m: qp.QConnectMessage) -> None:
        t = m.message_type
        if t == qp.MESSAGE_TYPE_SRVR_RNDR_SET_ACTIVE:
            await self._set_active(m.srvr_rndr_set_active.active)
        elif t == qp.MESSAGE_TYPE_SRVR_CTRL_SESSION_STATE:
            self.queue.note_version(m.srvr_ctrl_session_state.queue_version)
            self.ask_queue_state()
        elif t == qp.MESSAGE_TYPE_SRVR_RNDR_SET_STATE:
            await self._on_set_state(m.srvr_rndr_set_state)
        elif t == qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_LOADED:
            await self._on_queue_loaded(m.srvr_ctrl_queue_tracks_loaded)
        elif t == qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_STATE:
            self.queue.set_state(m.srvr_ctrl_queue_state)
            await self.port.queue_changed(self.queue, self.item_id)
        elif t in _RESYNC_ON:
            self.ask_queue_state()
        elif t == qp.MESSAGE_TYPE_SRVR_RNDR_SET_LOOP_MODE:
            self.loop = m.srvr_rndr_set_loop_mode.loop_mode
        elif t == qp.MESSAGE_TYPE_SRVR_RNDR_SET_MAX_AUDIO_QUALITY:
            q = m.srvr_rndr_set_max_audio_quality.max_audio_quality
            self._send(
                qp.QConnectMessage(
                    message_type=qp.MESSAGE_TYPE_RNDR_SRVR_MAX_AUDIO_QUALITY_CHANGED,
                    rndr_srvr_max_audio_quality_changed=qp.RndrSrvrMaxAudioQualityChanged(
                        max_audio_quality=q
                    ),
                )
            )
        # SET_VOLUME / MUTE_VOLUME: deliberately ignored — we advertise
        # VOLUME_REMOTE_CONTROL_NOT_ALLOWED (bit-perfect).

    async def _set_active(self, active: bool) -> None:
        self.active = active
        if not active:
            # Another device took over: stop our audio; the TUI keeps the mirror
            # as its local timeline (handled by active_changed).
            if self.port.is_running():
                self._pos_s = self.port.position_s()
                self.port.stop()
            self.state = PAUSED if self.item_id >= 0 else STOPPED
            self._loaded = False
        self.port.active_changed(active)
        if active:
            self.ask_queue_state()
            self.report()

    async def _on_set_state(self, s: qp.SrvrRndrSetState) -> None:
        if s.HasField("queue_version"):
            self.queue.note_version(s.queue_version)
        if s.HasField("next_track"):
            nt = s.next_track
            self._hint = QueueItem(nt.queue_item_id, nt.track_id) if nt.queue_item_id >= 0 else None
        if s.HasField("current_track"):
            ct = s.current_track
            if ct.queue_item_id < 0:
                self._stop()
            else:
                # Always a jump, even for the "same" item id (see module doc).
                start = s.current_position / 1000 if s.HasField("current_position") else 0.0
                state = s.playing_state if s.HasField("playing_state") else self.state
                await self._go(QueueItem(ct.queue_item_id, ct.track_id), start, state)
                return
        seeked = False
        if not s.HasField("current_track") and s.HasField("current_position"):
            await self.seek(s.current_position / 1000, report=False)
            seeked = True
        if s.HasField("playing_state"):
            await self._apply_playing(s.playing_state)
        # After a seek the player's polled position lags; report the target.
        self.report(position_s=self._pos_s if seeked else None)

    async def _on_queue_loaded(self, msg: qp.SrvrCtrlQueueTracksLoaded) -> None:
        start_item = self.queue.load(msg)
        if self.active and start_item is not None:
            # Verified: the app sends nothing else — we start it ourselves.
            await self._go(start_item, 0.0, PLAYING)
        await self.port.queue_changed(self.queue, self.item_id)
        if self.queue.shuffle:
            self.ask_queue_state()  # the shuffle order only comes with a snapshot

    # ── local transport (keys / media keys / queue rows) ──────────────────────

    async def toggle_pause(self) -> None:
        if self.item_id < 0:
            return
        await self._apply_playing(PAUSED if self.state == PLAYING else PLAYING)
        self.report()

    async def seek(self, pos_s: float, report: bool = True) -> None:
        pos_s = max(0.0, pos_s)
        if self.port.is_running() and self._loaded:
            self.port.seek(pos_s)
        self._pos_s = pos_s
        if report:
            self.report(position_s=pos_s)

    async def jump(self, item_id: int) -> None:
        item = self.queue.find(item_id)
        if item is not None:
            await self._go(item, 0.0, PLAYING)

    async def previous(self) -> None:
        item = self.queue.previous_before(self.item_id)
        if item is not None:
            await self._go(item, 0.0, PLAYING)

    async def track_ended(self) -> None:
        """Natural end (or an unplayable track): advance like a real renderer."""
        self._loaded = False
        nxt = self.queue.next_after(self.item_id, self.loop) or self._hint
        self._hint = None
        if nxt is None:
            self.state = STOPPED
            self._pos_s = 0.0
            self.report()
            return
        await self._go(nxt, 0.0, PLAYING)

    # ── reporting ─────────────────────────────────────────────────────────────

    def report(self, position_s: float | None = None) -> None:
        if not self.active:
            return
        if position_s is None:
            position_s = self.port.position_s() if self.port.is_running() else self._pos_s
        nxt = self.queue.next_after(self.item_id, self.loop) or self._hint
        st = qp.QueueRendererState(
            playing_state=self.state,
            buffer_state=qp.BUFFER_STATE_OK,
            current_position=qp.Position(timestamp=now_ms(), value=int(position_s * 1000)),
            duration=int(self.port.duration_s() * 1000),
            queue_version=self.queue.version,
            current_queue_item_id=self.item_id,
            next_queue_item_id=nxt.item_id if nxt else -1,
        )
        self._send(
            qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_RNDR_SRVR_STATE_UPDATED,
                rndr_srvr_state_updated=qp.RndrSrvrStateUpdated(state=st),
            )
        )

    def ask_queue_state(self) -> None:
        self._send(
            qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_CTRL_SRVR_ASK_FOR_QUEUE_STATE,
                ctrl_srvr_ask_for_queue_state=qp.CtrlSrvrAskForQueueState(
                    queue_version_ref=self.queue.version, action_uuid=uuid.uuid4().bytes
                ),
            )
        )

    # ── internals ─────────────────────────────────────────────────────────────

    async def _go(self, item: QueueItem, start_s: float, state: int) -> None:
        """Make ``item`` current and bring the player in line with ``state``.
        Unplayable tracks are skipped forward (bounded by the queue length)."""
        for _ in range(len(self.queue.play_order()) + 1):
            if self.port.is_running():
                self.port.stop()
            self.item_id, self.track_id = item.item_id, item.track_id
            self._pos_s = start_s
            self._loaded = False
            self.state = state
            play = state == PLAYING
            ok = await self.port.load_track(item.track_id, start_s, play)
            self._loaded = play and ok
            if ok or not play:
                break
            nxt = self.queue.next_after(item.item_id)
            if nxt is None:
                self.state = STOPPED
                break
            item, start_s = nxt, 0.0
        await self.port.queue_changed(self.queue, self.item_id)
        self.report(position_s=start_s)

    async def _apply_playing(self, state: int) -> None:
        if state == PLAYING:
            if self.port.is_running() and self._loaded:
                self.port.set_paused(False)
            elif self.item_id >= 0:
                if not await self.port.load_track(self.track_id, self._pos_s, True):
                    await self.track_ended()  # unplayable: skip like _go does
                    return
                self._loaded = True
        elif state == PAUSED:
            if self.port.is_running() and self._loaded:
                self._pos_s = self.port.position_s()
                self.port.set_paused(True)
        elif state == STOPPED:
            self._stop()
            return
        self.state = state

    def _stop(self) -> None:
        if self.port.is_running():
            self.port.stop()
        self.state = STOPPED
        self.item_id, self.track_id = -1, 0
        self._pos_s = 0.0
        self._loaded = False
