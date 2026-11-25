# Architecture

How qobit is put together, for someone about to change it. CLAUDE.md is the
rule book (invariants, pitfalls that each cost a bug); this is the map. When a
refactor moves a responsibility, update the table below in the same PR.

## Layers

```
 qobit (CLI)  ─────────────►  __main__.py        commands, TUI launcher
                                   │
 ui/ (Textual)                     ▼
   screens/ widgets/        what the user sees: read the app through
        │                   public names only (tests/test_ui_boundary.py)
        ▼
   app.py  QobitApp         the one App: reactive state, tabs, key actions,
        │                   the wiring between the parts below
        ├── play_context.py     timeline/cursor writers (local or Connect)
        ├── lyrics_controller.py lyrics lookup, line, prefetch, retry, offset
        └── (playback engine: being extracted from app.py, see below)
        │
 no Textual below this line (importable and testable on their own)
   audio/      mpv process + IPC, device list, bit-perfect check, media keys
   qobuz/      API client, models, stream resolution (streams.py), OAuth
   connect/    Qobuz Connect renderer (protocol, session, queue mirror)
   lyrics/     LRC parsing, matching, cache, providers, service
   config, store, token_store, library_cache, account_data, _fs, doctor
```

The shared look (themes, nav, transport bar, art, list widgets, row mixins)
imports only Textual and itself; `tests/test_ui_boundary.py` fails on an import
of the app, `screens/`, `qobuz/`, `config` or the state modules there.

## Who owns what

| State | Owner | Read by |
|---|---|---|
| Timeline (`_timeline`) and cursor | `LocalPlayContext` / `ConnectPlayContext` (the only writers) | screens via `app.history`, `up_next`, `current_track`, `queue_position` |
| Playing track, position, pause, quality, signal lamp | reactives on `QobitApp` | every widget (self-wiring `watch()`) |
| mpv process, stop generation | `audio/player.py` `MpvPlayer` | the poll loop via `snapshot()` |
| Stream URL resolution and the quality memory | `qobuz/streams.py` `StreamResolver` (`app.streams`) | `_play_stream`, gapless prefetch |
| Lyrics, offset, lookup/retry/prefetch state | `lyrics_controller.py` | widgets via `app.lyrics`, `lyrics_offset`; reactives `lyrics_status/line/version` stay on the app |
| Favourites, playlists | caches on `QobitApp` (`ensure_*`), disk copy in `library_cache` | screens via `app.favorite_*`, `user_playlists` |
| Session token | `token_store` (keyring, else `config.json`) | `QobuzClient` |
| Persistent runtime state | `store` (player/queue JSON in `DATA_DIR`) | the app at start and on change |

## Playback, end to end

1. A screen asks the app to play (`play_track`, `jump_to`, `_advance_queue`); the
   active play context moves the timeline/cursor and calls `_do_play`.
2. `_play_stream` resolves the stream (`StreamResolver.resolve`: quality
   fallback, previews and restrictions told apart), starts mpv with the URL
   from a private playlist file, and `_begin_track` updates the reactives.
3. The poll loop (a thread, 2 Hz) reads mpv over IPC and writes position,
   pause and play state back through `call_from_thread`. End of track is "same
   stop generation and mpv no longer running"; a bumped generation is a stop.
4. Late in a track the next stream is appended to mpv (gapless); mpv moving on
   by itself shows as `playlist-pos` rising and the cursor follows.
5. Under Qobuz Connect the server owns the queue and the renderer owns
   playback; transport actions go through the renderer.

The bit-perfect invariant (no resampling, no DSP) is enforced in
`audio/player.py` (mpv flags) and checked in `audio/verify.py`.

## Extraction in progress

`ui/app.py` was one 2,600-line file. It is being split into collaborators that
hold one concern each and talk to the app through a small host interface, so
each can be tested without the UI. Done, in merge order:

| Step | Moved to | What stayed in the app |
|---|---|---|
| App stylesheet | `ui/app_css.py` (`BASE_CSS`, `QOBIT_CSS`) | `CSS = BASE_CSS + QOBIT_CSS` |
| Reads by screens | public properties on `QobitApp` | the underscore state |
| Shared-layer imports | `formatting.py`, `ICON_FAV` in `_now_playing.py` | nothing |
| Lyrics | `ui/lyrics_controller.py` | reactives, keys, panel/page switching |
| Stream resolution | `qobuz/streams.py` | `_play_stream` (what starting a track means) |

Next: the end-of-track and gapless state machine, then the poll loop and
transport actions. A step is done when the app only wires the collaborator and
its tests run without a `QobitApp`.

## How a refactor is checked

- `tests/render_dump.py` dumps every rendered cell (text and style) for a set
  of scenes and themes; run before and after, `diff -rq` must be empty for a
  change that must not alter the look.
- A new guard test is added with each boundary, with a probe showing the guard
  can fail.
- Timing-sensitive tests poll (`wait_until`, `quiet`, `press_until`); never a
  fixed pause.
