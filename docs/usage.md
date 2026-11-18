# Using qobit

All keys, and how each tab works. The README has a short version.

## Logging in

`qobit auth` prints a Qobuz login link and opens it in your browser (when
there is a display). Log in, and the browser comes back to qobit on
`localhost`; the session token is saved in the system keyring (the macOS
Keychain, or GNOME Keyring / KWallet on Linux), or in
`~/.config/qobit/config.json` (readable only by you) where there's none,
such as a headless machine. `QOBIT_KEYRING=0` keeps it in the file. Over SSH
into a desktop machine the keyring isn't reachable: qobit asks you to log in
there (that session then lives in the file).

On a machine without a browser (over SSH, a headless box), open the link on
any device and log in. The browser then lands on a `localhost` page that
doesn't load: copy that page's address from the address bar and paste it into
the terminal where `qobit auth` is waiting. The login waits 5 minutes.

You can also log in from inside qobit: started without a session, or when
the session expires, it opens a login dialog with the same link (`c` copies
it, `o` opens it, or click it) and a field for the pasted address (pasting
anywhere in the dialog works too). `Esc` closes it; `^p` → *Log in to
Qobuz* opens it again. Your queue is kept either way.

`qobit auth status` shows who is logged in and checks that the saved session
still works.

`qobit logout`, or `^p` → *Log out of Qobuz*, erases everything the account
left on this machine: the session, the queue and its history, the last
track, the mixes, the saved copy of the library and the cached covers. Settings (audio device, theme,
volume) stay; `qobit logout --all` deletes those too, leaving nothing of
qobit. `qobit logout` refuses while qobit is running (it would write the
queue back): log out from inside it instead. If a different account logs in
after a session expires, the previous account's data is erased the same way.

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
| `Escape` | Back one level: close an album inside an artist page, then the page; close a detail view; or clear the filter (the footer shows `esc Back` when there's somewhere to go back to). Otherwise focus the tab bar |
| `-` / `=` | Software volume down / up, only once it's turned on from `Ctrl+P` (they appear in the footer then) |
| `Ctrl+P` | Command palette: theme, transparent background, Endless radio, Qobuz Connect, software volume |
| `q` | Quit |

Albums, artists and playlists you open from Search, `g`, `i` or Similar
artists open as a page on top of the tab. The tab bar stays visible (the tab
you came from is lit) and a trail under it shows where you are, e.g.
`Search › Mogwai › Mr. Beast`: click a name to go back there. `1`–`7`, or a
click on a tab, closes the pages and switches tab.

The footer lists only the keys that apply where you are: Sort and Filter
disappear while an album or artist is open, for example.

Themes: qobit's own `qobit-cyberdeck`, `ayu-dark`, `ayu-mirage` and `ayu-light`
(colours from the official [ayu palette](https://github.com/ayu-theme/ayu-colors)),
plus Textual's built-in themes. `Ctrl+P` → *Theme*; the choice is saved.
`qobit-cyberdeck` fills the progress bar with a cyan-to-magenta gradient; the
others use a flat accent.

The transport bar's bottom-right readout shows the stream's resolution,
`HI-RES` (in a colour of its own) when it's above CD quality, and a
**bit-perfect lamp** once the output has been checked, a couple of seconds into
each track: `◉ BIT-PERFECT`, or what differs (`◌ RESAMPLED 96 → 48 kHz`). See
[audio.md](audio.md#verifying). Album cards and the album page mark hi-res
albums the same way. The playing track's `▶` is a small animated bar meter
while it plays (decoration: qobit doesn't analyse the audio). Covers that are
still loading, or missing, show a tile with initials.

## Playback (`1`)

qobit opens here, on the queue you had when you last closed it, paused on the
last track. `Space` resumes from where you left off.

The **left** side is one list: **Recently Played** (`↺`), then the
**now-playing** track (▶/⏸), then **Up Next**. Selecting a row moves playback
there without removing anything. Tracks you skip stay under Recently Played; if
you jump back, the tracks in between return to Up Next.
Playing an album or playlist from the library replaces Up Next and keeps your
history. When a track ends, the cursor moves with playback only if it was on
the playing track; if you've moved it, it stays where you put it. The same goes
for every other list.

The **right** side is the **Now Playing** panel: cover art, title and artist,
`album · year`, `genre · label`, the stream's resolution (`[ 24-BIT · 96 kHz ]`), and the artist's
biography (scroll with the mouse wheel). Below ~100 columns the panel is hidden
and the timeline takes the full width; on a short terminal the cover shrinks,
and hides when there's no room, so the text always shows.

| Key | Action |
|-----|--------|
| `c` | Clear Up Next |
| `X` | Clear Recently Played |
| `f` | Favourite the highlighted track (or the now-playing one when the panel has focus) |

The OS **previous** media key restarts the track in its first 3 seconds and
steps back in history after that.

Tracks play **gaplessly**: the next one is lined up in the last 30 seconds.
Where the sample rate changes between two tracks, the output reopens for the
new rate (a short pause: qobit doesn't resample). Switch it off with
`qobit --no-gapless` (this run) or "Gapless playback" in the command palette
(Ctrl+P, saved); while it is on, the Playback panel title reads
`QUEUE · GAPLESS`. See
[Audio setup](audio.md#gapless-playback).

## Library: Tracks, Artists, Albums, Playlists (`2`–`5`)

| Key | Action |
|-----|--------|
| `s` | Cycle the sort key (Date Added → Artist/Name → Title/Album → Year → …) |
| `r` | Reverse the sort |
| `/` | Live filter: type to narrow the list (every letter goes into the filter, `i` and `f` too) |
| `Enter` | While typing: done. The results stay, and `i`, `f`, Enter (play or open) act on them again |
| `Escape` | Clear the filter |

The border shows the filter: `/ query_  ·  ⏎ done` while typing, `⌕ query`
while results are still narrowed. The first result is selected as you type. Favourite tracks show a `♥` in every track list except
Tracks (where everything is one).

An artist's page (from the Artists tab, Search, `g` or `i`) shows their **Top
Tracks** and, if you have favourited any of their songs, **Your favourites**.
On a wide terminal the two lists sit side by side and `←` `→` move between
them; on a narrower one they share a panel: `t` (or `←` `→`) switches, or
click the list's name in the panel title.

Below them, **Albums & EPs** shares its panel with **Similar artists** (from
Qobuz): `t` or a click on the name in the panel title switches, `Enter` opens
an artist, `i` follows them, and `♥` marks artists you already follow. From 128
columns the header also lists a few similar artists beside the bio. Artists Qobuz has no similar artists for show
only their albums.

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

## Cover art

qobit draws cover art with the Kitty graphics protocol where the terminal
supports it (kitty, Ghostty) and with coloured half blocks elsewhere. To
choose, run `qobit --images MODE` for one run, or pick "Images: …" in the
command palette (`Ctrl+P`) to save it:

| Mode | What you get |
|---|---|
| `auto` | the default: Kitty graphics if supported, else blocks |
| `kitty` | Kitty graphics, even if the terminal didn't say it supports them (WezTerm, Konsole: may not work) |
| `blocks` | coloured half blocks, two pixels per character cell |
| `off` | initials instead of cover art; no covers downloaded (the OS media widget still gets one per track) |

On a slow machine, `off` is the lightest, then `blocks`.

## Slow machines (Raspberry Pi and similar)

qobit runs on small boards such as a Raspberry Pi 4 with 2 GB of RAM. To
keep it quick there:

- **Animations off.** In the command palette, pick "Animations: turn off".
  The wordmark stops changing colour and the playing track shows ▶ instead
  of the bar meter. qobit also turns them off when `TEXTUAL_ANIMATIONS=none`
  is set.
- **Lighter cover art.** `blocks` costs less than `kitty`, and `off` costs
  nothing (see [Cover art](#cover-art)).
- **Memory.** qobit keeps at most 48 full-size covers in memory, and each
  grid card keeps a small copy at its own size. The rest stay on disk
  (`~/.cache/qobit/images`, up to 200 MB). Scrolling a library of hundreds
  of albums uses about 300 MB.

## Media keys and now-playing widgets

On macOS qobit appears in Now Playing / Control Center; on Linux it publishes
an MPRIS player (desktop widgets, media keys, `playerctl`):

```bash
playerctl -p qobit metadata   # title / artist / album / artUrl while playing
```

## Files

```
~/.config/qobit/config.json            login (the token itself: system keyring if any), audio device, theme, settings
~/.local/share/qobit/player_state.json last track + position
~/.local/share/qobit/queue_state.json  the queue (history, current, up next)
~/.local/share/qobit/mixes_cache.json  DailyQ / WeeklyQ
~/.local/share/qobit/connect.lock      held while Qobuz Connect is on
~/.local/share/qobit/qobit.lock        held while qobit runs (qobit logout refuses then)
~/.local/share/qobit/mpv.sock          mpv IPC socket
~/.local/share/qobit/stream.m3u        the playing stream's URL, while playing
~/.cache/qobit/images/                 cover-art cache
~/.cache/qobit/library/                your favourites and playlists, as last fetched
```

`qobit clear-cache` deletes the cover-art cache and the saved copy of the
library (`--covers` or `--library` for just one). Both are safe to delete:
qobit fetches them again when it needs them. Quit qobit before clearing the
library: a running qobit keeps it in memory and would save it again.

`qobit logout` erases the session, the account's `.json` files and both
caches; `qobit logout --all` removes everything listed here.
