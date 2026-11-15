# Changelog

All notable changes to qobit. Versions follow [semver](https://semver.org/);
while in 0.x, a minor bump means new features and a patch bump means fixes.

Releasing: bump `version` in `pyproject.toml` (the only place it lives), add
an entry here, `uv lock`, commit, then tag `vX.Y.Z` and create the GitHub
release from this entry.

## [0.4.0] — 2026-11-14

### Added

- `g` — go to the artist or album of the track that's playing (a small popup;
  also works on the paused track restored after a restart).

### Changed

- Documentation rewritten: a compact README (Qobuz Connect now front and
  centre) with the detail moved to `docs/usage.md` (every key, each tab) and
  `docs/audio.md` (bit-perfect setup, quality, verification). New logo.

### Fixed

- CI: install the system headers PyGObject/pycairo build against (they have
  been required for Linux media keys since 0.1.x), so lint and tests run again.

## [0.3.0] — 2026-11-14

### Added

- **Qobuz Connect phase 2:** while qobit is the active Connect device you can
  edit the queue from qobit too — play from the library, play next, add to
  queue, remove, clear, song radio (`R`). Changes go through Qobuz, so qobit
  and the app always show the same queue.
- **Playback first:** the Queue tab is now **Playback**, the first tab, and
  qobit opens on it. Tabs are labelled with their key — `[1] Playback`,
  `[2] Tracks` … `[7] Search` (compact `1 Playback` below 90 columns).
- **The queue survives restarts:** history, current track and Up Next are
  restored on launch, paused where you left off.

### Fixed

- Albums, Artists and Playlists tiles are the same, slightly smaller size, and
  playlist covers are sharp (qobit used 50 px thumbnails).
- One selection style for all three grids. With a transparent background the
  selection tint was hidden behind the card's text — likely the cause of the
  long-standing "selection looks wrong" issue.
- Qobuz Connect: a second qobit on the same computer no longer knocks the first
  out of the apps' device list — only one can have Connect on, and a dropped
  registration rejoins by itself.

## [0.2.2] — 2026-11-14

### Fixed

- Qobuz Connect: the Qobuz apps showed "unknown quality" while qobit played.
  qobit now reports the real stream format (sample rate, bit depth, CD/Hi-Res
  level) on every track; file and device format are the same, since output
  is bit-perfect.

## [0.2.1] — 2026-11-14

### Fixed

- A track could be skipped right after a stop-and-restart (e.g. changing
  track): stopping mpv could be mistaken for the track ending naturally.
- Qobuz Connect: when an app (or the qobuz-mcp AI remote) moved playback to
  qobit and started music in one go, the track could restart and pause itself.

## [0.2.0] — 2026-11-14

### Added

- **Qobuz Connect** (phase 1, opt-in). qobit appears in the official Qobuz
  apps' device picker; pick it there and the app becomes the remote while
  qobit plays bit-perfect through mpv. Enable from the command palette (saved)
  or for one run with `qobit --connect-on`. The transport bar shows
  `[ ⇄ CONNECT ]` / `[ ⇄ CONNECT · ACTIVE ]`.
  - Play, pause, seek, skip and picking songs/albums/playlists from the app;
    the Queue tab mirrors the app's queue, including edits made in the app.
  - Local control keeps working while active (space, seek, previous/next,
    media keys, selecting a Queue row) and is reflected in the app.
  - Queue *edits* from qobit while active are not supported yet (phase 2) —
    qobit says so instead.
  - The app's volume control is ignored on purpose (bit-perfect).
  - When another device takes over, qobit keeps the queue as its own, paused.
  - Unofficial: speaks the community-documented protocol
    (`docs/qobuz-connect.md`).

### Changed

- Queue/timeline writes go through a play-context layer
  (`ui/play_context.py`); local behaviour is unchanged.
- New dependencies: `protobuf`, `websockets`.

## [0.1.1] — 2026-11-14

### Fixed

- Tracks the account can't stream are no longer played as 30-second MP3
  previews. Qobuz returns the preview at every quality, so the fallback chain
  used to accept it silently, breaking the bit-perfect FLAC guarantee. The TUI
  now shows "Not streamable (preview only)" and skips to the next track;
  `qobit play` exits with an error.

## [0.1.0] — 2026-11-14

Baseline release, tagged retroactively: everything built before qobit had
releases (development phases 1–9).

- Bit-perfect playback through mpv: exclusive `hw:` ALSA / PipeWire
  rate-following on Linux, CoreAudio exclusive mode on macOS, post-play
  rate/format verification. Opt-in software volume, off by default.
- Textual TUI: Tracks (sortable, filterable), Artists and Albums grids with
  inline detail, Playlists, Search, Queue with Now Playing hero.
- Timeline + cursor queue: skip back and forth without dropping tracks, play
  next / add to queue / remove, per-track context menu (`i`).
- Qobuz song radio and endless radio; DailyQ / WeeklyQ mixes.
- Favourites with heart glyphs across all track lists.
- OS media controls: macOS Now Playing, Linux MPRIS2, with artwork.
- Kitty-protocol cover art with on-disk cache; themes incl. the qobit theme,
  transparent background.
- Session restore (last track + position).
- CLI: `play`, `auth`, `devices`, `set-device`, `clear-cache`.

[0.4.0]: https://github.com/pierdom/qobit/releases/tag/v0.4.0
[0.3.0]: https://github.com/pierdom/qobit/releases/tag/v0.3.0
[0.2.2]: https://github.com/pierdom/qobit/releases/tag/v0.2.2
[0.2.1]: https://github.com/pierdom/qobit/releases/tag/v0.2.1
[0.2.0]: https://github.com/pierdom/qobit/releases/tag/v0.2.0
[0.1.1]: https://github.com/pierdom/qobit/releases/tag/v0.1.1
[0.1.0]: https://github.com/pierdom/qobit/releases/tag/v0.1.0
