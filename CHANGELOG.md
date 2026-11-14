# Changelog

All notable changes to qobit. Versions follow [semver](https://semver.org/);
while in 0.x, a minor bump means new features and a patch bump means fixes.

Releasing: bump `version` in `pyproject.toml` (the only place it lives), add
an entry here, `uv lock`, commit, then tag `vX.Y.Z` and create the GitHub
release from this entry.

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

[0.1.0]: https://github.com/pierdom/qobit/releases/tag/v0.1.0
