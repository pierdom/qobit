# Using qobit

All keys, and how each tab works. The README has a short version.

## Global keys

| Key | Action |
|-----|--------|
| `1` … `7` | Tabs: Playback, Tracks, Artists, Albums, Playlists, Mixes, Search (the top bar shows `[N] Name`; below 90 columns it shortens to `N Name`) |
| `Space` | Pause / resume |
| `[` / `]` | Seek back / forward 10 s (or click the progress bar) |
| `g` | Go to the artist or album of the track that's playing (`1` artist, `2` album) |
| `i` | Menu for what's highlighted. A track: Play next, Add to queue, Play radio, add/remove from favourites, Go to artist, Go to album (+ Remove from queue on Up Next rows). An album (a card, or the album page): add/remove from favourites. An artist (a card, or the artist page): follow/unfollow |
| `f` | Favourite / unfavourite the highlighted track |
| `R` | Song radio from the current track |
| `Enter` | Open the selected item, or play the selected track |
| `↑` `↓` `←` `→` | Move in lists and grids |
| `PageUp` / `PageDown` | Move the selection a page at a time |
| `Home` / `End` | First / last item |
| `Escape` | Back / close filter / focus the tab bar |
| `Ctrl+P` | Command palette: theme, transparent background, Endless radio, Qobuz Connect, software volume |

Themes: qobit's own `qobit-cyberdeck`, `ayu-dark`, `ayu-mirage` and `ayu-light`
(colours from the official [ayu palette](https://github.com/ayu-theme/ayu-colors)),
plus Textual's built-in themes. `Ctrl+P` → *Theme*; the choice is saved.
| `q` | Quit |

## Playback (`1`)

qobit opens here, on the queue you had when you last closed it, paused on the
last track. `Space` resumes from where you left off.

The **left** side is one list: **Recently Played** (`↺`), then the
**now-playing** track (▶/⏸), then **Up Next**. Selecting a row moves playback
there without removing anything. Tracks you skip stay under Recently Played; if
you jump back, the tracks in between return to Up Next.
Playing an album or playlist from the library replaces Up Next and keeps your
history.

The **right** side is the **Now Playing** panel: cover art, title and artist,
`album · year`, `genre · label`, the stream's resolution, and the artist's
biography (scroll with the mouse wheel). Below ~100 columns the panel is hidden
and the timeline takes the full width.

| Key | Action |
|-----|--------|
| `c` | Clear Up Next |
| `X` | Clear Recently Played |
| `f` | Favourite the highlighted track (or the now-playing one when the panel has focus) |

The OS **previous** media key restarts the track in its first 3 seconds and
steps back in history after that.

## Library: Tracks, Artists, Albums, Playlists (`2`–`5`)

| Key | Action |
|-----|--------|
| `s` | Cycle the sort key (Date Added → Artist/Name → Title/Album → Year → …) |
| `r` | Reverse the sort |
| `/` | Live filter: type to narrow the list |
| `Escape` | Close the filter, then clear it |

The border shows the filter: `/ query_` while typing, `⌕ query` while results
are still narrowed. Favourite tracks show a `♥` in every track list except
Tracks (where everything is one).

## Radio and mixes

**Song radio** (`R`) fills Up Next with ~50 Qobuz suggestions seeded from the
current track and what you've recently heard. **Endless radio** (`Ctrl+P` →
*Endless radio*) refills the queue automatically when it runs dry; the setting
persists.

**Mixes** (`6`) builds a personal **DailyQ** (`d`) and **WeeklyQ** (`w`) from
your listening history and favourites, cached for a day and a week. You can also
pick a card with `Tab` or `←` `→` and open it with `Enter`; `↓` moves to its
tracks. A brand-new account needs some listening first.

## Search (`7`)

Type and press `Enter`; results are split into tracks, albums and artists.
`Enter` on a result plays the track or opens the album/artist.

## Qobuz Connect

Turn it on from `Ctrl+P` → *Qobuz Connect* (saved) or with
`qobit --connect-on` (this run only). qobit then appears in the Qobuz apps'
device picker. Pick it there to control qobit from the app. While qobit is
the active device:

- You can play, pause, seek, skip and pick music in the app. The Playback tab
  shows the app's queue.
- The same actions work in qobit (transport keys, selecting a row, playing from
  the library, play next, add, remove, clear, song radio). Changes show up in
  the app within about a second.
- The app's volume slider is ignored on purpose: software volume would change
  the bits the DAC receives.
- Autoplay suggestions added by the app can't be removed from qobit yet, and
  Endless radio doesn't refill while Connect is active.

When another device takes over, qobit keeps the queue as its own, paused. Only
one qobit per computer can have Connect on. The transport bar shows
`[ ⇄ CONNECT ]` when qobit is visible in the apps and `[ ⇄ CONNECT · ACTIVE ]`
once it's the playing device.

How it works: [qobuz-connect.md](qobuz-connect.md) (protocol) and
[qobuz-connect-design.md](qobuz-connect-design.md) (how qobit shares the queue
with the server).

## Media keys and now-playing widgets

On macOS qobit appears in Now Playing / Control Center; on Linux it publishes
an MPRIS player (desktop widgets, media keys, `playerctl`):

```bash
playerctl -p qobit metadata   # title / artist / album / artUrl while playing
```

## Files

```
~/.config/qobit/config.json            login, audio device, theme, settings
~/.local/share/qobit/player_state.json last track + position
~/.local/share/qobit/queue_state.json  the queue (history, current, up next)
~/.local/share/qobit/mixes_cache.json  DailyQ / WeeklyQ
~/.local/share/qobit/connect.lock      held while Qobuz Connect is on
~/.local/share/qobit/mpv.sock          mpv IPC socket
~/.cache/qobit/images/                 cover-art cache (safe to delete: qobit clear-cache)
```
