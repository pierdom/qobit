"""ServerQueue — qobit's read-only mirror of the Qobuz Connect queue.

The server owns the queue (docs/qobuz-connect-design.md). This model only
*applies* what the server says; it never invents structure. Two update paths:

- ``load()`` for ``SRVR_CTRL_QUEUE_TRACKS_LOADED`` — the fast path, because
  picking a song in an app is exactly this message and playback must start
  from it immediately (verified against the iOS app: no SET_STATE follows).
- ``set_state()`` for ``SRVR_CTRL_QUEUE_STATE`` — the full snapshot. Every
  other edit (insert/add/remove/reorder/shuffle/autoplay) is handled by asking
  for this snapshot rather than replaying the delta locally: one code path,
  nothing to drift.

No current item here: the queue state carries none. The renderer owns which
item is playing.
"""

from __future__ import annotations

from dataclasses import dataclass

from .proto import qconnect_pb2 as qp

LOOP_OFF = qp.LOOP_MODE_OFF
LOOP_ONE = qp.LOOP_MODE_REPEAT_ONE
LOOP_ALL = qp.LOOP_MODE_REPEAT_ALL


@dataclass(frozen=True)
class QueueItem:
    item_id: int
    track_id: int
    autoplay: bool = False


class ServerQueue:
    def __init__(self) -> None:
        self.version: qp.QueueVersion = qp.QueueVersion()
        self.items: list[QueueItem] = []
        self.autoplay: list[QueueItem] = []
        self.shuffle: bool = False
        self._shuffled: list[int] = []  # indexes into items, in play order

    # ── updates ───────────────────────────────────────────────────────────────

    def note_version(self, version: qp.QueueVersion) -> None:
        """Track the newest version seen; reports and commands must echo it."""
        if (version.major, version.minor) >= (self.version.major, self.version.minor):
            self.version = qp.QueueVersion(major=version.major, minor=version.minor)

    def load(self, msg: qp.SrvrCtrlQueueTracksLoaded) -> QueueItem | None:
        """Replace the queue. Returns the item at ``queue_position`` (where the
        controller wants playback to start), or None if out of range."""
        self.note_version(msg.queue_version)
        self.items = [QueueItem(t.queue_item_id, t.track_id) for t in msg.tracks]
        if msg.autoplay_reset:
            self.autoplay = []
        self.shuffle = msg.shuffle_mode if msg.HasField("shuffle_mode") else self.shuffle
        # A loaded queue in shuffle mode comes with a seed, not an order — the
        # order arrives with the next QUEUE_STATE. Until then, play queue order.
        self._shuffled = []
        pos = msg.queue_position
        return self.items[pos] if 0 <= pos < len(self.items) else None

    def set_state(self, msg: qp.SrvrCtrlQueueState) -> None:
        self.note_version(msg.queue_version)
        self.items = [QueueItem(t.queue_item_id, t.track_id) for t in msg.tracks]
        self.autoplay = [QueueItem(t.queue_item_id, t.track_id, True) for t in msg.autoplay_tracks]
        self.shuffle = msg.shuffle_mode
        idx = list(msg.shuffled_track_indexes)
        valid = len(idx) == len(self.items) and sorted(idx) == list(range(len(self.items)))
        self._shuffled = idx if (self.shuffle and valid) else []

    # ── queries ───────────────────────────────────────────────────────────────

    def play_order(self) -> list[QueueItem]:
        """What will actually play, in order: the queue (shuffled if the server
        gave us a shuffle order), then autoplay suggestions."""
        base = [self.items[i] for i in self._shuffled] if self._shuffled else list(self.items)
        return base + self.autoplay

    def find(self, item_id: int) -> QueueItem | None:
        for it in self.play_order():
            if it.item_id == item_id:
                return it
        return None

    def next_after(self, item_id: int, loop: int = LOOP_OFF) -> QueueItem | None:
        """The item that follows ``item_id`` at natural end of track."""
        order = self.play_order()
        ids = [it.item_id for it in order]
        if item_id not in ids:
            return None
        i = ids.index(item_id)
        if loop == LOOP_ONE:
            return order[i]
        if i + 1 < len(order):
            return order[i + 1]
        if loop == LOOP_ALL and order:
            return order[0]
        return None

    def previous_before(self, item_id: int) -> QueueItem | None:
        order = self.play_order()
        ids = [it.item_id for it in order]
        if item_id not in ids or ids.index(item_id) == 0:
            return None
        return order[ids.index(item_id) - 1]
