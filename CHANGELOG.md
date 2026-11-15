# Changelog

All notable changes to qobit. Versions follow [semver](https://semver.org/);
while in 0.x, a minor bump means new features and a patch bump means fixes.

Releasing: bump `version` in `pyproject.toml` (the only place it lives), add
an entry here, `uv lock`, commit, then tag `vX.Y.Z` and create the GitHub
release from this entry.

## [Unreleased]

### Added

- The artist page shows **Your favourites**: your favourite tracks by that
  artist, next to Top Tracks. Side by side on wide terminals; on narrower ones
  they share a panel and `t` switches. Hidden when you have none.

### Fixed

- Artists Qobuz has no photo for (about 1 in 10, e.g. Besso0) showed an empty
  tile. They now show the cover of their newest release, in the Artists tab
  and on the artist page.
- An album or artist sent with a missing (null) field could fail to load.

## [0.5.0] - 2026-11-15

### Added

- The `i` menu works on whatever is highlighted, not just track rows:
  - tracks: add to / remove from favourites;
  - albums (Albums tab, an artist's albums, Search, an open album page): add
    to / remove from favourites;
  - artists (Artists tab, Search, an open artist page): follow / unfollow.
- Ayu themes: Ayu Dark, Ayu Mirage and Ayu Light.
- Mixes: the DailyQ and WeeklyQ cards can be reached from the keyboard (`Tab`,
  `←` `→`, `Enter` to open, `↓` to the tracks).
- A logo, and a short CONTRIBUTING guide.

### Changed

- Visual pass: every border and divider is rounded, one accent colour
  throughout, one selection style for lists and grids. Lists no longer show a
  lighter box behind them when the background isn't transparent.
- The breadcrumb on a page opened with `g` or `i` names the page you came
  from (the album or artist you were on, or the tab).
- Tab subtitles show how many items there are (`↓ Date Added · 700`).
- Plainer documentation, a Terminal section in the README (images need the
  Kitty graphics protocol), a clearer disclaimer, and new screenshots.
- `LICENSE` now holds the full GPL-3.0 text.
- Minimum dependencies match what qobit needs: Textual 8.2, textual-image 0.12.

### Fixed

- Tracks, Artists, Albums and Playlists stopped at 200 items, so the oldest
  favourites couldn't be reached. Long lists now load more as you scroll, and
  `End` goes to the real last item.
- Artist pages showed at most 100 releases and playlists at most 500 tracks.
- qobit closed itself on a network error, e.g. a search while offline.
- "Go to album" did nothing for tracks started from an album page.
- Album and artist pages opened with `g` or `i` sometimes had no artist info
  or albums.
- Cover art was missing for the paused track restored after a restart.
- Picking two tracks quickly could play the first while the queue showed the
  second.
- Quickly opening one artist after another could mix their top tracks, and a
  previous track's cover could stay on screen.
- One failed favourites load at startup hid every favourite heart until
  restart.
- After `End` on a long Tracks list, the next tab could stay empty.

## [0.4.0] - 2026-11-14

### Added

- `g` opens a small popup to go to the artist or album of the track that's
  playing. It also works on the paused track restored after a restart.

### Changed

- Documentation rewritten: a shorter README, with the detail moved to
  `docs/usage.md` (every key, each tab) and `docs/audio.md` (bit-perfect setup,
  quality, verification). New logo.

### Fixed

- CI installs the system headers that PyGObject and pycairo build against
  (required for Linux media keys since 0.1.x), so lint and tests run again.

## [0.3.0] - 2026-11-14

### Added

- Qobuz Connect phase 2: while qobit is the active Connect device, you can
  also edit the queue in qobit (play from the library, play next, add to queue,
  remove, clear, song radio with `R`). Changes go through Qobuz, so qobit and
  the app show the same queue.
- The Queue tab is now called Playback. It's the first tab and qobit opens on
  it. Tabs show their key: `[1] Playback`, `[2] Tracks` … `[7] Search`
  (`1 Playback` below 90 columns).
- The queue is saved: history, current track and Up Next are restored on
  launch, paused where you left off.

### Fixed

- Albums, Artists and Playlists tiles are the same, slightly smaller size, and
  playlist covers are sharp (qobit used 50 px thumbnails).
- One selection style for all three grids. With a transparent background, the
  selection tint was hidden behind the card's text. This was probably the
  cause of the long-standing "selection looks wrong" issue.
- Qobuz Connect: a second qobit on the same computer no longer removes the
  first from the apps' device list. Only one can have Connect on, and qobit
  rejoins by itself if its registration is dropped.

## [0.2.2] - 2026-11-14

### Fixed

- Qobuz Connect: the Qobuz apps showed "unknown quality" while qobit played.
  qobit now reports the real stream format (sample rate, bit depth, CD/Hi-Res
  level) on every track; file and device format are the same, since output
  is bit-perfect.

## [0.2.1] - 2026-11-14

### Fixed

- A track could be skipped right after a stop-and-restart (e.g. changing
  track): stopping mpv could be mistaken for the track ending naturally.
- Qobuz Connect: when a remote control moved playback to qobit and started
  music in one go, the track could restart and then pause.

## [0.2.0] - 2026-11-14

### Added

- Qobuz Connect (phase 1, opt-in). qobit appears in the Qobuz apps' device
  picker; pick it there to control qobit from the app, while qobit plays
  bit-perfect through mpv. Enable from the command palette (saved)
  or for one run with `qobit --connect-on`. The transport bar shows
  `[ ⇄ CONNECT ]` / `[ ⇄ CONNECT · ACTIVE ]`.
  - Play, pause, seek, skip and picking songs/albums/playlists from the app;
    the Queue tab mirrors the app's queue, including edits made in the app.
  - Local control keeps working while active (space, seek, previous/next,
    media keys, selecting a Queue row) and is reflected in the app.
  - Editing the queue in qobit while it's active isn't supported yet
    (phase 2); qobit shows a message instead.
  - The app's volume control is ignored on purpose (bit-perfect).
  - When another device takes over, qobit keeps the queue as its own, paused.
  - Unofficial: speaks the community-documented protocol
    (`docs/qobuz-connect.md`).

### Changed

- Queue/timeline writes go through a play-context layer
  (`ui/play_context.py`); local behaviour is unchanged.
- New dependencies: `protobuf`, `websockets`.

## [0.1.1] - 2026-11-14

### Fixed

- Tracks the account can't stream are no longer played as 30-second MP3
  previews. Qobuz returns the preview at every quality, so the fallback chain
  used to accept it silently, breaking the bit-perfect FLAC guarantee. The TUI
  now shows "Not streamable (preview only)" and skips to the next track;
  `qobit play` exits with an error.

## [0.1.0] - 2026-11-14

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

[0.5.0]: https://github.com/pierdom/qobit/releases/tag/v0.5.0
[0.4.0]: https://github.com/pierdom/qobit/releases/tag/v0.4.0
[0.3.0]: https://github.com/pierdom/qobit/releases/tag/v0.3.0
[0.2.2]: https://github.com/pierdom/qobit/releases/tag/v0.2.2
[0.2.1]: https://github.com/pierdom/qobit/releases/tag/v0.2.1
[0.2.0]: https://github.com/pierdom/qobit/releases/tag/v0.2.0
[0.1.1]: https://github.com/pierdom/qobit/releases/tag/v0.1.1
[0.1.0]: https://github.com/pierdom/qobit/releases/tag/v0.1.0
