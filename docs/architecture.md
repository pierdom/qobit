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
        ├── gapless.py           the next track queued in mpv, mpv moving on to it
        └── (poll loop, play/stop, transport: still in app.py, see below)
        │
 no Textual below this line (importable and testable on their own)
   audio/      mpv process + IPC, device list, bit-perfect check, media keys
   qobuz/      API client, models, stream resolution, radio and mixes, OAuth
   connect/    Qobuz Connect renderer (protocol, session, queue mirror)
   lyrics/     LRC parsing, matching, cache, providers, service
   library (favourites in memory), library_cache (on disk), config, store,
   token_store, account_data, _fs, doctor
```

The shared look (themes, nav, transport bar, art, list widgets, row mixins)
imports only Textual and itself; `tests/test_ui_boundary.py` fails on an import
of the app, `screens/`, `qobuz/`, `config` or the state modules there.

## Who owns what

| State | Owner | Read by |
|---|---|---|
| Timeline (`_timeline`) and cursor | `LocalPlayContext` / `ConnectPlayContext` (the only writers) | screens via `app.history`, `up_next`, `current_track`, `queue_position` |
| Playing track, position, pause, quality, signal lamp | reactives on `QobitApp` | every widget (self-wiring `watch()`) |
| mpv process, stop generation | `audio/player.py` `MpvPlayer` | `audio/watch.py` `PlayerWatch` (one poll tick: position, pause, moved on, natural end vs stop); the app's poll thread applies it |
| Radio and mix suggestions | `qobuz/suggestions.py` `SuggestionService` (`app.suggestions`) | `app._fetch_radio`, `app.generate_mix` (they supply history and favourites) |
| Stream URL resolution and the quality memory | `qobuz/streams.py` `StreamResolver` (`app.streams`) | `_play_stream`, gapless prefetch |
| The track queued in mpv behind the playing one, prefetch state | `gapless.py` `GaplessQueue` (`app.gapless_queue`) | the app (`watch_queue_version`, `_play_stream`, the poll) |
| Lyrics, offset, lookup/retry/prefetch state | `lyrics_controller.py` | widgets via `app.lyrics`, `lyrics_offset`; reactives `lyrics_status/line/version` stay on the app |
| Favourites, followed artists, playlists | `library.py` `Library` (`app.library`): loading, toggling, background refresh; disk copy in `library_cache` | screens via `app.library.tracks`, `track_ids`, `albums`, `artists`, `playlists` |
| Session token | `token_store` (keyring, else `config.json`) | `QobuzClient` |
| Persistent runtime state | `store` (player/queue JSON in `DATA_DIR`) | the app at start and on change |

## Playback, end to end

1. A screen asks the app to play (`play_track`, `jump_to`, `_advance_queue`); the
   active play context moves the timeline/cursor and calls `_do_play`.
2. `_play_stream` resolves the stream (`StreamResolver.resolve`: quality
   fallback, previews and restrictions told apart), starts mpv with the URL
   from a private playlist file, and `_begin_track` updates the reactives.
3. The poll loop (a thread, 2 Hz) asks `PlayerWatch.read()` for a `Reading` of
   mpv and writes position, pause and play state back through
   `call_from_thread`. End of track is "same stop generation and mpv no longer
   running"; a bumped generation is a stop.
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
| Radio and mixes | `qobuz/suggestions.py` | the two thin methods that gather history and favourites |
| Poll tick and end-of-track rule | `audio/watch.py` | the poll thread (`_poll_player`): applies a `Reading` to the reactives, the 5 s state save |
| Grid navigation and lazy cards | `ui/widgets/grid.py` (`GridNav`), cards in `screens/cards.py` | the Albums, Artists, Playlists tabs and the artist page |
| Library (favourites, playlists) | `library.py` | `toggle_*` (flash), `_on_library_change` (redraws tabs and hearts) |
| Gapless state and logic | `ui/gapless.py` | `step_to_next` (cursor + transport), `next_track`, `is_local`, `extend_timeline` |

Next: the context menu. The transport
actions and `_play_stream` stay in the app: they are thin glue over the player
and the play contexts. A step is done when the app only wires the collaborator
and its tests run without a `QobitApp`.

## How a refactor is checked

- `uv run mypy` is clean and runs in CI; a widget reads the app through
  `qapp(self)` and mixins derive from `WidgetBase` (see CLAUDE.md, Development).
- `tests/render_dump.py` dumps every rendered cell (text and style) for a set
  of scenes and themes; run before and after, `diff -rq` must be empty for a
  change that must not alter the look.
- A new guard test is added with each boundary, with a probe showing the guard
  can fail.
- Timing-sensitive tests poll (`wait_until`, `quiet`, `press_until`); never a
  fixed pause.
