"""ConnectRenderer — qobit's behaviour as a Qobuz Connect renderer.

Split of authority (docs/qobuz-connect-design.md):

- **The server owns the queue.** ``ServerQueue`` mirrors it. Local edits are
  sent as ``CTRL_SRVR_QUEUE_*`` commands (``queue_*`` methods) and only show
  up once the server echoes them.
- **The renderer owns playback.** Commands from the server, and local transport
  keys, act on the player immediately and are *reported* back with
  ``RNDR_SRVR_STATE_UPDATED``. Natural end of track is the renderer's job too.

Protocol facts this relies on, all verified against the iOS app (see the
"Verified end-to-end" table in docs/qobuz-connect.md):

- ``SET_STATE`` is a delta. ``current_track`` means *jump to it* unless it
  names the item already held **in the same queue version** — queue item ids
  restart at 0 on every new queue, so the id alone never decides. (The server
  re-sends the current item with its activation handover, sometimes *after* a
  queue load already started it; reloading then would restart/stop it.)
- Picking a song in an app is only ``QUEUE_TRACKS_LOADED``; no ``SET_STATE``
  follows. The active renderer must start ``tracks[queue_position]`` itself.

UI-agnostic: it drives a ``PlayerPort`` (implemented by the TUI, or a fake in
tests) and sends through a ``send`` callable.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
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


# Qobuz format_id → Connect quality level. Same mapping qobuz-proxy uses with
# the official apps (5 MP3, 6 CD/16-bit FLAC, 7 hi-res ≤96 kHz, 27 hi-res 192 kHz).
_FORMAT_LEVEL = {
    5: qp.AUDIO_QUALITY_MP3,
    6: qp.AUDIO_QUALITY_CD,
    7: qp.AUDIO_QUALITY_HIRES_LEVEL1,
    27: qp.AUDIO_QUALITY_HIRES_LEVEL2,
}


# Server echoes of queue commands carry the command's action_uuid; the next
# queued command may only go out once its predecessor is echoed (each command
# is stamped with the queue version it was based on).
_ECHOES = {
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_LOADED: "srvr_ctrl_queue_tracks_loaded",
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_INSERTED: "srvr_ctrl_queue_tracks_inserted",
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_ADDED: "srvr_ctrl_queue_tracks_added",
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_REMOVED: "srvr_ctrl_queue_tracks_removed",
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_TRACKS_REORDERED: "srvr_ctrl_queue_tracks_reordered",
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_CLEARED: "srvr_ctrl_queue_cleared",
    qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_ERROR_MESSAGE: "srvr_ctrl_queue_error_message",
}
# An unanswered command stops blocking the line after this long.
_ECHO_TIMEOUT_S = 10.0


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
    def queue_error(self, message: str) -> None:
        """The server refused a queue command (the mirror is re-synced)."""


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
        # Queue major version in force when the current item became current.
        # Item ids restart at 0 per queue, so (item, track) alone can't tell
        # "the item we already have" from "item 0 of a brand-new queue".
        self._item_major = -1
        # Phase 2: local queue edits as CTRL_SRVR_QUEUE_* commands, one in
        # flight at a time (see _ECHOES). Each entry builds its message with
        # the queue version current *when it is sent*, not when it was queued.
        # A builder may return None ("nothing to do any more") and is skipped.
        self._pending: deque[Callable[[bytes], qp.QConnectMessage | None]] = deque()
        self._in_flight: tuple[bytes, float] | None = None

    # ── server → renderer ─────────────────────────────────────────────────────

    async def handle(self, m: qp.QConnectMessage) -> None:
        t = m.message_type
        released = self._on_echo(t, getattr(m, _ECHOES[t])) if t in _ECHOES else False
        await self._handle(m, t)
        # Send the next queued command only *after* this message is applied — a
        # LOADED echo must update the queue model before an edit queued behind
        # it (e.g. "Play radio" = load + replace) picks its target items.
        if released:
            self._dispatch()

    async def _handle(self, m: qp.QConnectMessage, t: int) -> None:
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
        same = False  # current_track names the item we already hold
        if s.HasField("current_track"):
            ct = s.current_track
            major = s.queue_version.major if s.HasField("queue_version") else self._item_major
            same = (
                ct.queue_item_id >= 0
                and ct.queue_item_id == self.item_id
                and ct.track_id == self.track_id
                and major == self._item_major
            )
            if ct.queue_item_id < 0:
                self._stop()
            elif same:
                # The item we already hold, same queue — e.g. the server's
                # delayed activation handover arriving after a queue load had
                # already started it. Reloading would restart (and, with the
                # handover's PAUSED, stop) the track. Apply position/state only.
                pass
            else:
                # A different item, or the same item id in a newer queue (ids
                # restart at 0 per queue): jump to it.
                start = s.current_position / 1000 if s.HasField("current_position") else 0.0
                state = s.playing_state if s.HasField("playing_state") else self.state
                await self._go(QueueItem(ct.queue_item_id, ct.track_id), start, state)
                return
        seeked = False
        if (same or not s.HasField("current_track")) and s.HasField("current_position"):
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

    def report_quality(
        self, format_id: int, sampling_rate_hz: int, bit_depth: int, channels: int = 2
    ) -> None:
        """Tell the apps what is playing — without it they show "unknown
        quality". Sent as both the *file* format and the *device* format: for
        qobit they're identical by design (bit-perfect: the DAC receives exactly
        the file's samples)."""
        if not self.active:
            return
        self._send(
            qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_RNDR_SRVR_FILE_AUDIO_QUALITY_CHANGED,
                rndr_srvr_file_audio_quality_changed=qp.RndrSrvrFileAudioQualityChanged(
                    sampling_rate=sampling_rate_hz,
                    bit_depth=bit_depth,
                    nb_channels=channels,
                    audio_quality=_FORMAT_LEVEL.get(format_id, qp.AUDIO_QUALITY_UNKNOWN),
                ),
            ),
            qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_RNDR_SRVR_DEVICE_AUDIO_QUALITY_CHANGED,
                rndr_srvr_device_audio_quality_changed=qp.RndrSrvrDeviceAudioQualityChanged(
                    sampling_rate=sampling_rate_hz, bit_depth=bit_depth, nb_channels=channels
                ),
            ),
        )

    # ── local queue edits → server commands (phase 2) ────────────────────────
    #
    # Nothing here touches the mirror: the server's echo triggers the usual
    # resync (QUEUE_STATE snapshot, or LOADED's own fast path), so the queue a
    # user sees is always the one every other app sees too.

    def queue_load(self, track_ids: list[int], start_index: int = 0) -> None:
        """Replace the queue; the echo (LOADED) makes this renderer start
        ``track_ids[start_index]`` — the same path as picking music in an app."""
        self._enqueue(
            lambda action: qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_CTRL_SRVR_QUEUE_LOAD_TRACKS,
                ctrl_srvr_queue_load_tracks=qp.CtrlSrvrQueueLoadTracks(
                    queue_version_ref=self.queue.version,
                    action_uuid=action,
                    track_ids=track_ids,
                    queue_position=start_index,
                    context_uuid=uuid.uuid4().bytes,
                ),
            )
        )

    def queue_insert(self, track_ids: list[int], after_item_id: int | None) -> None:
        """Insert after ``after_item_id`` (None = at the front)."""

        def build(action: bytes) -> qp.QConnectMessage:
            cmd = qp.CtrlSrvrQueueInsertTracks(
                queue_version_ref=self.queue.version,
                action_uuid=action,
                track_ids=track_ids,
                context_uuid=uuid.uuid4().bytes,
            )
            if after_item_id is not None and after_item_id >= 0:
                cmd.insert_after = after_item_id
            return qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_CTRL_SRVR_QUEUE_INSERT_TRACKS,
                ctrl_srvr_queue_insert_tracks=cmd,
            )

        self._enqueue(build)

    def queue_add(self, track_ids: list[int]) -> None:
        self._enqueue(
            lambda action: qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_CTRL_SRVR_QUEUE_ADD_TRACKS,
                ctrl_srvr_queue_add_tracks=qp.CtrlSrvrQueueAddTracks(
                    queue_version_ref=self.queue.version,
                    action_uuid=action,
                    track_ids=track_ids,
                    context_uuid=uuid.uuid4().bytes,
                ),
            )
        )

    def queue_remove(self, item_ids: list[int]) -> None:
        if not item_ids:
            return
        self._enqueue(
            lambda action: qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_CTRL_SRVR_QUEUE_REMOVE_TRACKS,
                ctrl_srvr_queue_remove_tracks=qp.CtrlSrvrQueueRemoveTracks(
                    queue_version_ref=self.queue.version,
                    action_uuid=action,
                    queue_item_ids=item_ids,
                ),
            )
        )

    def queue_replace_upcoming(self, track_ids: list[int]) -> None:
        """Swap everything after the current item for ``track_ids`` (song
        radio). Which items to remove is decided *when the command is sent*,
        from this renderer's queue model — an edit queued right behind a load
        (menu "Play radio" = load + replace) must target the new queue, not the
        one on screen when it was requested."""

        def remove_upcoming(action: bytes) -> qp.QConnectMessage | None:
            order = self.queue.play_order()
            ids = [it.item_id for it in order]
            cur = ids.index(self.item_id) if self.item_id in ids else -1
            upcoming = [it.item_id for it in order[cur + 1 :] if not it.autoplay]
            if not upcoming:
                return None
            return qp.QConnectMessage(
                message_type=qp.MESSAGE_TYPE_CTRL_SRVR_QUEUE_REMOVE_TRACKS,
                ctrl_srvr_queue_remove_tracks=qp.CtrlSrvrQueueRemoveTracks(
                    queue_version_ref=self.queue.version,
                    action_uuid=action,
                    queue_item_ids=upcoming,
                ),
            )

        self._enqueue(remove_upcoming)
        self.queue_add(track_ids)

    def _enqueue(self, build: Callable[[bytes], qp.QConnectMessage | None]) -> None:
        self._pending.append(build)
        self._dispatch()

    def _dispatch(self) -> None:
        if self._in_flight is not None:
            if time.monotonic() - self._in_flight[1] < _ECHO_TIMEOUT_S:
                return
            log.warning("connect: queue command unanswered, moving on")
            self._in_flight = None
        while self._pending:
            action = uuid.uuid4().bytes
            message = self._pending.popleft()(action)
            if message is None:
                continue  # nothing left to do for this one
            self._in_flight = (action, time.monotonic())
            self._send(message)
            return

    def _on_echo(self, t: int, body) -> bool:
        """Bookkeeping for a queue echo. Returns True when it answered our
        in-flight command and the next one may go (the caller sends it once the
        message is fully applied)."""
        # Every echo carries the queue version it produced; take it before the
        # next command goes out, or that command is stamped stale and refused.
        if body.HasField("queue_version"):
            self.queue.note_version(body.queue_version)
        if self._in_flight is None or bytes(body.action_uuid) != self._in_flight[0]:
            return False  # another controller's change (or late echo) — resync handles it
        self._in_flight = None
        if t == qp.MESSAGE_TYPE_SRVR_CTRL_QUEUE_ERROR_MESSAGE:
            # Like the official apps: drop what was waiting, re-ask the state.
            self._pending.clear()
            self.port.queue_error(body.error.message or body.error.code or "queue error")
            self.ask_queue_state()
            return False
        return True

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
            self._item_major = self.queue.version.major
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
