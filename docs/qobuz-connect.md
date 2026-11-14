# Qobuz Connect — how it works and how qobit can speak it

> **Implemented.** This is the protocol reference, written while reverse-
> engineering it with a throwaway spike. The real implementation lives in
> `src/qobit/connect/` (shipped in qobit 0.2–0.3: renderer, queue mirror and
> queue edits from qobit); how it's put together is in
> [qobuz-connect-design.md](qobuz-connect-design.md). The "Not covered by the
> spike" and "Integration notes" sections below are kept as the original
> analysis — most of them are now done.

Status: **spike verified 2026-11-14** (`spikes/qobuz_connect/connect_spike.py`).
qobit's existing OAuth session is enough to join the user's Connect session as a
renderer, show up in the official apps' device picker, become active, receive
the queue, and round-trip play / pause / seek / skip through the Qobuz cloud.
By default the spike's player is simulated; `--audio` plays through qobit's
bit-perfect `MpvPlayer`.

> Unofficial. Qobuz ships a Connect SDK only to hardware partners; everything
> below is reverse-engineered by the community. Schema can change whenever the
> web player does. Use at your own risk.

## Architecture in one picture

```
 Qobuz app (phone/desktop)          Qobuz cloud                     qobit
 ─────────────────────────   wss://qws-*.qobuz.com/ws   ──────────────────────
  controller  ──CTRL_SRVR_*──▶   session server   ──SRVR_RNDR_*──▶  renderer
              ◀─SRVR_CTRL_*───   (owns the queue)  ◀─RNDR_SRVR_*──  (mpv)
                                                                      │
                                       track/getFileUrl ◀─────────────┘
                                       (renderer fetches the FLAC itself)
```

Connect only moves **commands and state**. The renderer still resolves the
stream URL with its own credentials and plays the file itself, so qobit's
bit-perfect invariant is untouched: mpv still gets the FLAC bits directly.

## Step by step (what the spike does)

### 1. Mint a socket token

```
POST https://www.qobuz.com/api.json/0.2/qws/createToken
X-App-Id: <app_id>                    # from config.json oauth_session
X-User-Auth-Token: <user_auth_token>  # same
Content-Type: application/x-www-form-urlencoded

jwt=jwt_qws
```

Response: `{"jwt_qws": {"jwt": "...", "endpoint": "wss://qws-eu-prod.qobuz.com/ws", ...}}`.
Works with qobit's OAuth app id + token as-is (verified). **One socket per
token**: two sockets on the same token evict each other, so mint a fresh token
per connection / reconnect.

### 2. Open the websocket and handshake

Every binary websocket message is one or more frames:

```
[type: varint][length: varint][body: protobuf]  (repeated)
```

`type` is `qcloud.MessageType`: `AUTHENTICATE=1`, `SUBSCRIBE=2`,
`UNSUBSCRIBE=3`, `PAYLOAD=6`, `ERROR=9`, `DISCONNECT=10`.

Send, in one websocket message:

1. `Authenticate{msg_id, msg_date=now_ms, jwt}`
2. `Subscribe{msg_id, msg_date, proto=1 /* PROTO_QCONNECT */}`

`msg_id` is a per-connection incrementing counter.

### 3. Speak Connect inside `PAYLOAD` frames

Connect messages travel as `qcloud.Payload{proto=1, dests=[b"\x02"] /* backend
channel */, payload=QConnectBatch}`. A `QConnectBatch{messages_time,
messages_id (counter), messages: [QConnectMessage]}` wraps one or more
`QConnectMessage`s; each has a `message_type` plus the one matching sub-field.

Message-type prefixes tell you direction:

| Prefix | Direction | Examples |
|---|---|---|
| `CTRL_SRVR_*` (61–80) | controller → server | join, set player state, set active renderer, queue load/add/remove |
| `SRVR_CTRL_*` (81–105) | server → controllers | session state, add/remove renderer, renderer state, queue events |
| `SRVR_RNDR_*` (41–47) | server → renderer | set state, set active, set volume, set max quality |
| `RNDR_SRVR_*` (21–29) | renderer → server | state updated, volume changed, file quality changed |

### 4. Join the session

Renderers join **as a controller** that carries a device (this is what the
official apps do):

```
CTRL_SRVR_JOIN_SESSION { device_info: DeviceInfo{
    device_uuid  (16 bytes, stable per install),
    friendly_name, brand, model, type=DEVICE_TYPE_COMPUTER,
    capabilities{ min=MP3, max=HIRES_LEVEL3,
                  volume_remote_control=NOT_ALLOWED },   ← bit-perfect
    software_version } }
```

No `session_uuid` is needed: the server puts you in the session of the account
behind the token. It replies with:

- `SRVR_CTRL_ADD_RENDERER{renderer_id, device_info}`. When `device_uuid` is
  ours, this is our **registration**, so remember `renderer_id`.
- `SRVR_CTRL_SESSION_STATE{session_uuid, active_renderer_id, queue_version, playing_state, loop_mode}`

At this point the device is visible in the official apps' picker.

### 5. Become active (or wait to be picked)

Either the user picks the device in an app, or the device activates itself with
`CTRL_SRVR_SET_ACTIVE_RENDERER{renderer_id}` using its own id. Then:

- `SRVR_RNDR_SET_ACTIVE{active: true}`
- `SRVR_CTRL_ACTIVE_RENDERER_CHANGED{active_renderer_id}`
- `SRVR_RNDR_SET_STATE{...}` with the **existing queue position**. The previous
  device's queue is handed over.

⚠️ Self-activating takes over the account's live session. Whatever device was
playing stops being the active renderer.

### 6. Obey `SRVR_RNDR_SET_STATE` (a delta)

```
SrvrRndrSetState { optional playing_state, optional current_position (ms),
                   queue_version, current_track: QueueTrackRef,
                   next_track: QueueTrackRef }
```

Fields left out are unchanged. Observed:

| Received | Meaning |
|---|---|
| `current_track{queue_item_id, track_id}` (+ maybe position/state) | load/jump to this track |
| `current_position` only | seek |
| `playing_state` only | play / pause |
| `current_track.queue_item_id < 0` | stop |
| `next_track` | preload hint (gapless) |

**Gotchas (both bit the spike):**

- **`current_track` always means "jump to it", even when nothing seems to
  change.** `queue_item_id`s restart at 0 for every new queue, and proto3 omits
  the zero value, so `{"track_id": X}` with no item id means item 0. Re-picking
  the song that's playing also arrives as the same item. Never compare item ids
  to decide whether to reload. Reload whenever `current_track` is present.
- **Picking a new song or album in the app sends no play command.** The
  app sends `CTRL_SRVR_QUEUE_LOAD_TRACKS`, and the server broadcasts
  `SRVR_CTRL_QUEUE_TRACKS_LOADED{queue_version (major bumps), tracks:
  [{queue_item_id, track_id}], queue_position, shuffle_mode?}`. That's all.
  Verified against the real iOS app: no `SET_STATE` follows. The **active
  renderer must itself** jump to `tracks[queue_position]`, start playing, and
  report. Subsequent play/pause arrive as bare `SET_STATE{playing_state}`, so if
  you ignore the load you end up resuming the *old* track. Keep the track list:
  it's also how you know what comes next when `SET_STATE` didn't say.

`track_id` is a normal Qobuz track id, so qobit's `get_track` / `get_file_url`
apply unchanged.

### 7. Report state back

After every command, every state change, and every ~5 s while playing:

```
RNDR_SRVR_STATE_UPDATED { state: QueueRendererState{
    playing_state, buffer_state=OK,
    current_position: Position{timestamp=now_ms, value=pos_ms},
    duration (ms), queue_version (echo latest),
    current_queue_item_id, next_queue_item_id } }
```

Controllers extrapolate the position from `timestamp`. A renderer that says
it's playing without a current track gets paused by the server.

Also answer `SRVR_RNDR_SET_MAX_AUDIO_QUALITY` with
`RNDR_SRVR_MAX_AUDIO_QUALITY_CHANGED`. It's worth sending
`RNDR_SRVR_FILE_AUDIO_QUALITY_CHANGED{sampling_rate, bit_depth, nb_channels}`
once mpv reports the real format, so the apps show the right badge. That
dovetails with `audio/verify.py`.

### 8. Keepalive / reconnect

Send a websocket ping every 30 s. Treat about 60 s without a frame as a dead
connection. On reconnect, mint a **new** token and rejoin: renderer id and
active flag are lost until the server re-registers. `DISCONNECT{reconnect}`
says whether retrying is welcome. The web player backs off with delays of
100 ms, 200 ms, 500 ms, 1 s, 2 s, 5 s, 10 s, then 20 s for every later attempt.

## Observed trace (abridged, real run)

```
>> CTRL_SRVR_JOIN_SESSION
<< SRVR_CTRL_ADD_RENDERER        renderer_id=2  (ours)
<< SRVR_CTRL_SESSION_STATE       active_renderer_id=-1 queue_version=1.1 PAUSED
>> CTRL_SRVR_SET_ACTIVE_RENDERER 2
<< SRVR_RNDR_SET_ACTIVE          true
<< SRVR_CTRL_ACTIVE_RENDERER_CHANGED 2
<< SRVR_RNDR_SET_STATE           PAUSED current_track=230631237 next_track=item 1
>> CTRL_SRVR_SET_PLAYER_STATE    PLAYING     → << SRVR_RNDR_SET_STATE PLAYING
>> CTRL_SRVR_SET_PLAYER_STATE    pos=60000   → << SRVR_RNDR_SET_STATE current_position=60000
>> CTRL_SRVR_SET_PLAYER_STATE    item=1      → << SRVR_RNDR_SET_STATE current_track=item 1 (230631238)
>> CTRL_SRVR_SET_PLAYER_STATE    PAUSED      → << SRVR_RNDR_SET_STATE PAUSED
```

(`--drive` makes the spike act as its own controller on the same socket, so the
loop can be tested without a phone.)

## Verified end-to-end against the iOS app (2026-11-14, `--audio`)

| Action in the app | What arrived | Spike behaviour |
|---|---|---|
| Pick "qobit (spike)" | `SET_ACTIVE` + `SET_STATE{current_track, PAUSED}` | loads handed-over track, mpv not started |
| Play / pause | bare `SET_STATE{playing_state}` | mpv start / IPC pause |
| Pick a song (search, album, or tap in queue) | `QUEUE_TRACKS_LOADED{queue_position}` only | jumps to `tracks[pos]` and plays |
| Next / previous | `SET_STATE{current_track, next_track}` | reloads |
| Seek | `SET_STATE{current_position}` | mpv `seek absolute` |
| Track ends | nothing (renderer's job) | advances to next queue item, reports |

Seen but ignored: `SRVR_CTRL_VOLUME_CHANGED/_MUTED` and the
`*_AUDIO_QUALITY_CHANGED` broadcasts. One track resolved to a 30 s MP3 preview
(`fmt=5&range=20-30` in the URL) without the spike noticing. The real
implementation must detect previews and skip them.

## Running the spike

```bash
cd spikes/qobuz_connect
protoc --proto_path=proto --python_out=pb proto/qcloud.proto proto/qconnect.proto
uv run --no-project --with protobuf --with websockets --with httpx \
    python connect_spike.py                     # register, then pick "qobit (spike)" in the Qobuz app
#   ... --activate                              # make itself active (takes over the session!)
#   ... --activate --drive --seconds 30         # self-test: play/seek/skip/pause loop
#   ... --activate --load 8767428,10137214      # self-test: load a new queue exactly like the app
```

### With real audio (`--audio`)

```bash
cd spikes/qobuz_connect
uv run --with protobuf --with websockets python connect_spike.py --audio
```

Runs inside the project so it can import qobit's own `QobuzClient` +
`MpvPlayer`: same bit-perfect mpv flags and the device saved by
`qobit devices`/the TUI. The simulated clock stays the source of truth.
mpv starts only when told to PLAY (a paused handover doesn't blip), seek and
pause go through IPC, the reported position is read back from mpv `time-pos`,
and when mpv exits naturally the spike advances to `next_track` itself and
reports it. Don't run it alongside the qobit TUI: both use the same mpv IPC
socket.

It reads `~/.config/qobit/config.json` (`oauth_session.app_id/token`), so run
`qobit auth` first. It doesn't depend on any qobit code.

## Not covered by the spike

- **LAN discovery.** The apps also find devices on the LAN via mDNS
  `_qobuz-connect._tcp` plus three HTTP calls (`GET get-display-info`,
  `GET get-connect-info`, `POST connect-to-qconnect`). The last one hands over
  the session and the *app user's* tokens. Only needed to serve an account
  other than the one qobit is logged into. See `discovery.rs` in the reference
  crate. Needs an inbound TCP port and UDP 5353.
- **Controller role** (qobit driving a WiiM/Bluesound/etc.): it's the same
  socket. Send `CTRL_SRVR_QUEUE_LOAD_TRACKS` / `SET_PLAYER_STATE` /
  `SET_ACTIVE_RENDERER`. Queue changes carry `queue_version_ref` + an
  `action_uuid`, and the server echoes the uuid back in the matching queue
  event. Wait for it before sending the next queue change.
- **Play reporting**: `track/reportStreamingStart` and
  `track/reportStreamingEndJson` (needs the `blob` from `getFileUrl`). Keeps
  Qobuz history/Last.fm right. qobit doesn't send these today in any mode.

## Integration notes for the real implementation

- **Package:** `src/qobit/connect/` holding the generated protobuf code (commit
  the generated `_pb2.py` or generate at build time), a transport (framing,
  keepalive, reconnect with token re-mint), and a session (join, registration,
  queue_version tracking). New deps: `websockets`, `protobuf`; `zeroconf` only if
  LAN discovery is added.
- **Volume:** advertise `VOLUME_REMOTE_CONTROL_NOT_ALLOWED` and ignore
  `SRVR_RNDR_SET_VOLUME`. Never forward it to mpv `volume`. If a
  non-bit-perfect software-volume mode exists, gate it the same way the local
  volume is gated.
- **Queue ownership is the hard part.** In Connect the *server* owns the queue
  (`queue_version`, `queue_item_id`). While qobit is the active renderer,
  `_timeline`/`_cursor` must mirror the server queue
  (`CTRL_SRVR_ASK_FOR_QUEUE_STATE` → `SRVR_CTRL_QUEUE_STATE`, then incremental
  `SRVR_CTRL_QUEUE_TRACKS_*` events) instead of being edited locally. Local queue
  actions (play next, remove, radio) must become `CTRL_SRVR_QUEUE_*` commands.
  Likely shape: a "Connect play context" that drives the existing
  `jump_to`/`_do_play`, plus local transport keys sending
  `CTRL_SRVR_SET_PLAYER_STATE` instead of acting directly.
- **Natural end of track:** report the new state with the next item. The
  `_stop_gen` end-of-track logic is the right hook.
- **Autoplay vs qobit radio:** Connect has its own autoplay
  (`SRVR_CTRL_AUTOPLAY_*`). Pick one source of truth while Connect is active.
- **Gapless:** `next_track` in `SET_STATE` is the preload hint. That's
  roadmap phase 12.

## References

- [ciaens/qobuz-connect](https://github.com/ciaens/qobuz-connect) (Rust, MIT):
  cleanest protocol docs, source of `proto/`. Fork with more recent updates:
  [tsanona/qobuz-connect](https://github.com/tsanona/qobuz-connect).
- [leolobato/qobuz-proxy](https://github.com/leolobato/qobuz-proxy) (Python,
  MIT): Connect → DLNA bridge, closest to qobit's stack. Its local backend
  applies software volume, so it's **not** bit-perfect. Borrow protocol handling,
  not the audio path.
- [jrhahn/qonnect](https://github.com/jrhahn/qonnect): controller-only, LAN endpoints.
- [ahcm/qconnect](https://github.com/ahcm/qconnect): CLI renderer/controller.
