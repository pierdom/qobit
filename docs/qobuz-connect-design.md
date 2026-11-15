# Qobuz Connect in qobit: queue ownership design

Status: phases 1 and 2 are implemented (qobit 0.2–0.3): qobit works as a
Connect renderer with a mirror of the server's queue, and queue edits made in
qobit are sent to the server. Connect is opt-in (`connect_enabled`,
`qobit --connect-on`). Phases 3 and 4 are still proposals. This builds on the
spike described in [qobuz-connect.md](qobuz-connect.md).

Scope: qobit as a Connect renderer (the Qobuz app picks qobit, qobit plays
through mpv). qobit controlling other devices, and LAN discovery, are phase 4.

Where the code differs from the plan below, the "As built" notes say so.

## The problem

Today `QobitApp` owns the play context outright: one `_timeline: list[Track]`
plus `_cursor`, mutated in place by `play_track`, `jump_to`, `queue_next`,
`queue_last`, `remove_from_queue`, `clear_queue`, `clear_history`,
`_advance_queue` (incl. radio refill) and `_start_radio`, with `queue_version`
bumped after each.

In Connect the **server owns the queue**. Every controller (the phone, the
desktop app, qobit itself) proposes changes, and the server orders them, bumps
`queue_version` and broadcasts the result. If two places mutate the queue
independently they diverge silently: the phone shows one Up Next and qobit plays
another.

## Key observation: the Connect queue already *is* a timeline + cursor

The server queue is an ordered list of `(queue_item_id, track_id)` covering
past, current and upcoming items, plus a current item. That's exactly
`_timeline` + `_cursor`:

| qobit | Connect |
|---|---|
| `_timeline[i]` | `tracks[i]` → `(queue_item_id, track_id)` |
| `_cursor` | index of `current_queue_item_id` |
| `_history` / `_play_queue` slices | items before / after current |
| `queue_version` (UI refresh counter) | `queue_version{major, minor}` (server-authoritative) |
| `jump_to(i)` | play `tracks[i]` and report it |

So the Playback tab (`QueueView`), `NowPlayingHero`, `TransportBar` and
everything else that reads `_timeline`/`_cursor`/`queue_version` stay unchanged.
Only the code that writes the timeline changes.

## Decision: split ownership by concern

> **The server owns the queue. The renderer (qobit) owns playback.**

This is the hi-fi streamer model, and the spike already relies on it.

1. The server decides the queue's contents and order. While qobit is the
   active renderer, `_timeline` is a mirror rebuilt only from server queue
   events. Queue actions in qobit are sent as `CTRL_SRVR_QUEUE_*` commands and
   take effect when the server echoes them back, not before.
2. qobit decides playback (play, pause, seek, which item is current, end of
   track). It acts on mpv at once and reports `RNDR_SRVR_STATE_UPDATED`; the
   server passes that on to the controllers. The spike already worked this way
   at the end of a track, and the app followed.

Why not optimistic local queue edits plus reconciliation: `queue_version` is a
server-assigned compare-and-set token. A local edit racing a phone edit gets
`SRVR_CTRL_QUEUE_ERROR_MESSAGE`, and you'd have to roll back UI state. Waiting
for the echo costs one round trip (under a second in the spike logs) and needs
no rollback. The reference crate makes the same choice: it queues commands
until the echo of the previous `action_uuid` arrives, and on error it re-asks
for the full queue state.

Why not route pause/seek/next through the server: it would add round-trip
latency to every key press. Also, the renderer is the only party that knows the
true position and whether mpv actually started. The server already accepts
renderer-driven changes, as natural-end advance showed.

## Shape: a queue backend seam in `QobitApp`

Extract the timeline mutators behind one small interface. The current code
becomes the local implementation unchanged, and a Connect implementation sits
next to it.

```python
class PlayContext(Protocol):          # ui/play_context.py
    async def load(self, tracks: list[Track], start: int) -> None  # play_track(t, queue=…)
    async def jump_to(self, index: int) -> None
    def queue_next(self, track: Track) -> None
    def queue_last(self, track: Track) -> None
    def remove(self, index: int) -> None
    def clear_upcoming(self) -> None
    def clear_history(self) -> None
    async def on_track_end(self) -> None   # _advance_queue, incl. refill
```

- `LocalPlayContext`: the existing code, moved over unchanged, editing
  `app._timeline`/`app._cursor`.
- `ConnectPlayContext`: sends the matching command and returns. While qobit is
  active, its server-event handler is the only code that writes
  `app._timeline`/`app._cursor`.

As built: the methods take what the call sites already had. `load(track,
queue, start)`, `remove(track)` (matched by identity; every mirror row is its
own `Track` object), `clear_queue()`, and `replace_upcoming(tracks)` for song
radio. Commands go out one at a time; each waits for the echo of the previous
one (matched by `action_uuid`), because each carries the queue version it was
based on.

| Local call | Connect command | Applied on echo |
|---|---|---|
| `load(tracks, start)` (play album/track from library) | `CTRL_SRVR_QUEUE_LOAD_TRACKS{track_ids, queue_position=start}` | `QUEUE_TRACKS_LOADED` → renderer jumps to `tracks[pos]` (the same code path as a pick in the app) |
| `jump_to(i)` / next / prev | none: play locally, report `current_queue_item_id` | — |
| `queue_next(t)` | `CTRL_SRVR_QUEUE_INSERT_TRACKS{insert_after=current item}` | `QUEUE_TRACKS_INSERTED` |
| `queue_last(t)` | `CTRL_SRVR_QUEUE_ADD_TRACKS` | `QUEUE_TRACKS_ADDED` |
| `remove(i)` | `CTRL_SRVR_QUEUE_REMOVE_TRACKS{queue_item_ids}` | `QUEUE_TRACKS_REMOVED` |
| `clear_upcoming()` | `REMOVE_TRACKS{all upcoming ids}` (`CLEAR_QUEUE` would drop the current track too) | `QUEUE_TRACKS_REMOVED` |
| `clear_history()` | `REMOVE_TRACKS{all past ids}` | `QUEUE_TRACKS_REMOVED` |
| `on_track_end()` | none: advance locally, report | — |
| *(future)* reorder | `CTRL_SRVR_QUEUE_REORDER_TRACKS` | `QUEUE_TRACKS_REORDERED` |

The app's existing call sites (`play_track`, `jump_to`, the context-menu
handlers, `_poll_player`'s natural-end branch) call `self._ctx.<method>` instead
of touching `_timeline`. `queue_version += 1` moves into the contexts.

### Mirror needs `queue_item_id`s alongside `Track`s

`_timeline` holds `Track`s. Connect addresses items by `queue_item_id`, and the
same `track_id` can appear twice. So `ConnectPlayContext` keeps a parallel
`item_ids: list[int]` aligned 1:1 with `_timeline`. Queue rows already carry
`timeline_index`, so `remove(index)` resolves `item_ids[index]`, and no UI
widget needs to know about Connect.

### Hydration: track ids → `Track`

Server events carry only `track_id`s. `ConnectPlayContext` keeps a
`track_id → Track` cache and fetches misses concurrently via
`client.get_track` (bounded like `_images`' semaphore). The mirror is
published only once every row is hydrated, so the Playback tab never shows half
a queue. If a newer rebuild starts while fetching, the older result is thrown
away (a generation counter, like `_render_version`). Open question: is there a
batch track endpoint? Loading a 30-track album takes 30 calls.

As built: the mirror doesn't replay each queue event. It applies
`QUEUE_TRACKS_LOADED` directly (playback has to start from it) and, for every
other change, asks for a full `QUEUE_STATE` snapshot and rebuilds. One code
path, nothing to drift.

## Lifecycle: entering and leaving Connect

```
          picked in app / SET_ACTIVE(true)
 LOCAL ───────────────────────────────────────▶ CONNECT-ACTIVE
   ▲      stash local ctx; ASK_FOR_QUEUE_STATE        │
   │      → mirror; play/pause per SET_STATE          │
   │                                                  │
   └──────────────────────────────────────────────────┘
      SET_ACTIVE(false) / socket lost / user disconnects in qobit:
      stop mpv; keep the mirrored timeline as the new LOCAL timeline
      (paused at the current position), so you can carry on locally
      where the phone left off
```

- Connected but not active (the default once Connect is enabled): qobit plays
  locally as before. Local playback never takes over the account's session.
- Taking over from qobit (later): a "Play on Connect" action would be
  `SET_ACTIVE_RENDERER(self)` + `QUEUE_LOAD_TRACKS(current timeline, cursor)`.
- Reconnect while active: the renderer id and active flag are lost. Mint a new
  token, rejoin, re-register. As built, qobit falls back to LOCAL as soon as the
  socket drops, keeping the mirror.
- As built: only one qobit per machine may join (a lock file). Two instances on
  one host share a device id, and when either leaves, the server drops the
  device for both. qobit also rejoins if the server removes its registration
  while the socket is still open.

## Radio vs Connect autoplay

Phase 3, not built yet. Two "keep playing" mechanisms can't both run:

- While CONNECT-ACTIVE, qobit's `_advance_queue` refill is off. The server's
  autoplay (`SRVR_CTRL_AUTOPLAY_TRACKS_LOADED`, then
  `QUEUE_TRACKS_ADDED_FROM_AUTOPLAY` as they're consumed) is the source of
  truth. Autoplay tracks show in Up Next after the queue, styled as a separate
  section (a `SectionHeader` like History / Up Next).
- `radio_mode` toggle → `CTRL_SRVR_SET_AUTOPLAY_MODE`, so the phone's autoplay
  switch and qobit's stay in sync.
- `_start_radio(seed)` still uses qobit's own `get_dynamic_suggestions`, then
  sends the result as `QUEUE_ADD_TRACKS` (Connect only takes track ids, wherever
  they came from).

## Shuffle and repeat

Read-only so far: when `shuffle_mode` is on, the mirror is built in play order
using `shuffled_track_indexes`, so Up Next shows what will play. On natural end, `next_track` from the last `SET_STATE` wins over our own
list. Repeat (`SET_LOOP_MODE`) is honoured for `REPEAT_ONE` and `REPEAT_ALL` at
natural end. qobit gets no local shuffle UI until it has one in local mode
too.

## Bit-perfect

None of the above changes the signal path: Connect carries ids and commands,
and qobit still resolves `getFileUrl` and plays through `MpvPlayer`.

- Volume: qobit advertises `VOLUME_REMOTE_CONTROL_NOT_ALLOWED` and ignores
  `SET_VOLUME`. (Planned but not built: advertise `ALLOWED` when the user has
  turned on software volume, and route `SET_VOLUME` through the same setting as
  the local volume keys.)
- Format: qobit reports `RNDR_SRVR_FILE_AUDIO_QUALITY_CHANGED` and
  `DEVICE_AUDIO_QUALITY_CHANGED` from the stream's format when a track starts.
  The plan was to use mpv's measured output (`audio/verify.py`); that check
  only runs in `qobit play` today.
- Previews: 30-second MP3 previews (`sample: true` from `getFileUrl`) are
  treated as unplayable and skipped, in local mode too. qobit doesn't send a
  `PLAYBACK_ERROR` for them yet.

## Module layout

As built:

```
src/qobit/connect/
├── __init__.py       device_info(), acquire_instance_lock()
├── proto/            qcloud_pb2.py, qconnect_pb2.py (generated, committed) + .proto
├── wire.py           varint framing, Payload/Batch wrap/unwrap (pure)
├── session.py        websocket, token per connection, join, registration,
│                     keepalive, reconnect with backoff, rejoin if dropped
├── queue.py          ServerQueue: LOADED fast path + QUEUE_STATE snapshots
└── renderer.py       ConnectRenderer: playback state machine over a PlayerPort,
                      state reports, serialized queue commands
ui/play_context.py    LocalPlayContext, ConnectPlayContext
```

`connect/` has no Textual imports. The UI talks to it through
`ConnectPlayContext` and the `connect_state` reactive on `QobitApp`
(`off | connecting | ready | active | offline`, shown in the transport bar).

## Testing

- `wire.py`, `queue.py`: pure functions, plain unit tests.
- The renderer is tested against a fake player, replaying the message
  sequences seen live with the iOS app (`tests/test_connect.py`), and the TUI
  side through the real mirror code (`tests/test_connect_context.py`).
- The existing timeline tests still pass against `LocalPlayContext`, which
  shows the refactor didn't change local behaviour.
- Live checks: the iOS app, and a headless qobit driven by a controller script.

## Phasing

1. Done (0.2.0). Renderer with a mirror of the queue: the `connect/` package,
   the `PlayContext` split, the active/inactive lifecycle, transport keys acting
   on mpv and reporting. Queue edits in qobit were refused. Opt-in setting.
2. Done (0.3.0). Queue edits in qobit while active: the command table above,
   with echo handling and recovery from queue errors.
3. Autoplay and radio, shuffle/repeat display, play reporting
   (`reportStreamingStart`/`EndJson`, in local mode too).
4. qobit as a controller of other Connect devices, plus LAN discovery.

## Open questions

- Is there a batch endpoint to hydrate many track ids at once?
- Should `connect_enabled` default to on? Showing up in the apps whenever qobit
  runs is convenient but unexpected. It stays opt-in until phase 3 lands.

Answered: the server accepts "previous" from the renderer the same way as the
end-of-track advance. Two qobits on different machines appear as two devices;
two on the same machine conflict, which is why only one may join.
