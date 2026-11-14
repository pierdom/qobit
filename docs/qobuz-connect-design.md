# Qobuz Connect in qobit — queue ownership design

Status: **phase 1 implemented** (2026-11-14) — renderer + mirror-only queue,
opt-in (`connect_enabled`, `qobit --connect-on`). Phases 2–4 still proposals. Builds on the verified spike
(`docs/qobuz-connect.md`, `spikes/qobuz_connect/`). Scope: qobit as a Connect
**renderer** (the Qobuz app picks qobit, qobit plays through mpv). qobit
controlling *other* devices and LAN discovery are out of scope (phase 4).

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

So the Queue tab, `NowPlayingHero`, `TransportBar` and everything downstream of
`_timeline`/`_cursor`/`queue_version` can stay **unchanged**. Only *who writes*
the timeline changes.

## Decision: split ownership by concern

> **The server owns the queue. The renderer (qobit) owns playback.**

This is the hi-fi streamer model, and the spike already relies on it.

1. **Queue structure** (what's in it, in what order) is **server-authoritative**.
   While qobit is the active renderer, `_timeline` is a *mirror* rebuilt only
   from server queue events. Local queue actions are sent as `CTRL_SRVR_QUEUE_*`
   commands and take effect **when the echo arrives**, not optimistically.
2. **Playback** (play, pause, seek, which item is current, natural end) is
   **renderer-authoritative**. qobit acts on mpv immediately and *reports*
   `RNDR_SRVR_STATE_UPDATED`. The server relays it to the controllers. This is
   what the spike does on natural end, and the app followed (verified).

Why not optimistic local queue edits plus reconciliation: `queue_version` is a
server-assigned compare-and-set token. A local edit racing a phone edit gets
`SRVR_CTRL_QUEUE_ERROR_MESSAGE`, and you'd have to roll back UI state. Waiting
for the echo costs one round-trip (sub-second in the spike logs) and has no
rollback path. The reference crate makes the same choice: it queues commands
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

- `LocalPlayContext`: today's code moved over verbatim, mutating
  `app._timeline`/`app._cursor`.
- `ConnectPlayContext`: sends the matching command and returns. Its server-event
  handler is the **only** writer of `app._timeline`/`app._cursor` while active.

| Local call | Connect command | Applied on echo |
|---|---|---|
| `load(tracks, start)` (play album/track from library) | `CTRL_SRVR_QUEUE_LOAD_TRACKS{track_ids, queue_position=start}` | `QUEUE_TRACKS_LOADED` → renderer jumps to `tracks[pos]` (same path as the app: **one code path**) |
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
widget learns about Connect.

### Hydration: track ids → `Track`

Server events carry only `track_id`s. `ConnectPlayContext` keeps a
`track_id → Track` cache and fetches misses concurrently via
`client.get_track` (bounded like `_images`' semaphore). The mirror is
published only once every row is hydrated, so the Queue tab never shows half a
queue. Results are discarded if the queue `major` version moved while fetching:
the same pattern as `_render_version`. Open question: is there a batch track
endpoint? A 30-track album load is 30 calls today. Measure before optimising.

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
      (paused at the current position), so "pick up where the phone left off"
      works for free
```

- **Connected but not active** (the default once Connect is enabled): qobit
  plays locally exactly as today. Local playback **never** silently hijacks the
  account's session.
- **Taking over from qobit** (later): a "Play on Connect" action =
  `SET_ACTIVE_RENDERER(self)` + `QUEUE_LOAD_TRACKS(current timeline, cursor)`.
  Not needed for phase 1.
- **Reconnect while active:** the renderer id and active flag are lost. Mint a
  new token, rejoin, re-register. If the server doesn't re-activate us within a
  few seconds, fall back to LOCAL with the mirror kept (as above).

## Radio vs Connect autoplay

Two "keep it playing" mechanisms can't both run:

- While CONNECT-ACTIVE, qobit's `_advance_queue` refill is **off**. The server's
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

Phase 1 is **read-only**: when `shuffle_mode` is on, build the mirror in *play
order* using `shuffled_track_indexes`, so Up Next shows what will actually
play. On natural end, `next_track` from the last `SET_STATE` wins over our own
list. Repeat (`SET_LOOP_MODE`) is honoured for `REPEAT_ONE` and `REPEAT_ALL` at
natural end. qobit gets no local shuffle UI until it has one in local mode
too.

## Bit-perfect

Unchanged by all of the above: Connect moves ids and commands, and qobit still
resolves `getFileUrl` and plays through `MpvPlayer`. Specifically:

- Advertise `VOLUME_REMOTE_CONTROL_NOT_ALLOWED`. If the user enabled software
  volume (`config.get_software_volume_enabled()`), advertise `ALLOWED` and route
  `SET_VOLUME` through the *same* gate as the local volume keys, never around
  it.
- Report the real format via `RNDR_SRVR_FILE_AUDIO_QUALITY_CHANGED` from
  `audio/verify.py`'s mpv readback, not from what `getFileUrl` claimed.
- Detect 30 s previews (seen in the spike: `fmt=5&range=20-30`, MP3) and treat
  them as unplayable: report a `PLAYBACK_ERROR` and skip. Local mode has the
  same latent bug; fix it in `_do_play` for both.

## Module layout

```
src/qobit/connect/
├── proto/            qcloud_pb2.py, qconnect_pb2.py (generated, committed; regen script)
├── wire.py           varint framing, Payload/Batch wrap/unwrap (pure)
├── transport.py      wss + token mint (qws/createToken) + keepalive + reconnect/backoff
├── session.py        join, registration, active flag, queue_version, action_uuid
│                     echo tracking; emits typed events, sends typed commands
└── queue.py          ServerQueue: pure model applying LOADED/INSERTED/ADDED/
                      REMOVED/REORDERED/CLEARED/QUEUE_STATE/autoplay events
ui/play_context.py    PlayContext protocol, LocalPlayContext, ConnectPlayContext
```

`connect/` has no Textual imports. The UI talks to it only through
`ConnectPlayContext` and a handful of reactives on `QobitApp`
(`connect_state: off|connected|active`, shown in the transport bar).

## Testing

- `wire.py`, `queue.py`: pure, so plain unit tests.
- **Trace replay:** record raw socket frames during manual sessions (add a
  `--record` flag to the spike) and replay them into `Session` + `ServerQueue`.
  Assert the resulting `_timeline`/`_cursor`. The spike's logged
  pick-song / skip / seek / natural-end session is the first fixture.
- The existing timeline+cursor tests keep passing against `LocalPlayContext`.
  That's the guarantee the refactor didn't change local behaviour.
- Live manual check against the iOS app, as in the spike verification table.

## Phasing

1. **Renderer, mirror only.** `connect/` package, `PlayContext` extraction (no
   behaviour change), CONNECT-ACTIVE lifecycle, mirrored Queue tab, local
   transport keys acting on mpv and reporting. Local queue *mutations* while
   active are disabled with a flash ("Queue is controlled by Qobuz Connect").
   Opt-in setting `connect_enabled` (default off). Preview detection.
2. **Local queue edits while active:** the command table above, with echo
   handling and queue-error recovery (`ASK_FOR_QUEUE_STATE`).
3. **Autoplay ↔ radio, shuffle/repeat display, play reporting**
   (`reportStreamingStart`/`EndJson`, local mode too).
4. **qobit as controller** of other Connect devices, plus LAN discovery.

## Open questions

- Is there a batch endpoint to hydrate many track ids at once?
- Does the server accept renderer-driven *previous* (jump back) as readily as
  natural-end advance? Expected yes (same report), but verify in phase 1.
- Behaviour when two qobit instances (two machines) are both connected. Device
  uuid is per-host, so they should appear as two devices. Verify.
- Should `connect_enabled` eventually default to on? Being visible in the apps
  whenever qobit runs is convenient but also a surprise. Keep opt-in until phase 2 lands.
