# Changelog

All notable changes to qobit. Versions follow [semver](https://semver.org/);
while in 0.x, a minor bump means new features and a patch bump means fixes.

Releasing: bump `version` in `pyproject.toml` (the only place it lives), add
an entry here, `uv lock`, commit, then push the tag `vX.Y.Z`: the release
workflow publishes to PyPI and creates the GitHub release from this entry.

## [0.14.1] - 2026-11-20

### Fixed

- Capitalised text in square brackets from Qobuz (`[PIAS] Recordings`,
  `Abbey Road [Remastered]`) showed with the bracketed part missing on album
  and playlist pages, album and playlist tiles and the artist header. It is
  shown as it is now.
- Lyrics: a lookup that failed (a network blip as the track started) is asked
  again, up to three times, instead of staying "Couldn't reach the lyrics
  service" for the whole track.
- Lyrics: a timing shift or "remove lyrics" you made on a track restored from
  an earlier session is kept when the same track comes back with its ISRC.

## [0.14.0] - 2026-11-20

### Added

- **Lyrics.** The lyrics of the playing track, with the line being sung
  highlighted. The Playback tab shows them with the Now Playing details when
  there is room: side by side on a wide terminal, one above the other on a
  tall one, else `l` swaps the two. `L` opens the lyrics over the whole screen
  (`l` does too when both already show, or on a narrow terminal). `,` and `.`
  shift the timing by half a second for the track, `x` removes lyrics that are
  wrong, and both are remembered. Lyrics come from [LRCLIB](https://lrclib.net);
  timed ones follow the music, the rest show as scrollable text, and
  instrumentals say so. They are checked against the track (title, artist, and
  length within 2.5 s for timed ones) so another recording's don't show. They
  are looked up when a track starts, and for the next one near the end of this
  one, and cached in `~/.cache/qobit/lyrics`. Each lookup sends the track's
  artist, title, album and length to LRCLIB: `qobit --no-lyrics` turns it off
  for one run, "Lyrics" in the command palette saves the choice. Logout erases
  the cache and `qobit clear-cache --lyrics` clears it.

### Changed

- The Playback tab's columns are sized by the room: queue 60% and the right
  pane 40% in two columns; queue 40%, now playing 30% and lyrics 30% in three,
  the now-playing panel taking more of a wider terminal.
- Queue rows show the duration at the right edge. The Recently Played and Up
  Next rules give the count and the playing time, and the Up Next numbers are
  muted.
- The footer hides the keys that don't fit, last first, instead of cutting
  one in half.

## [0.13.0] - 2026-11-19

### Added

- **Shuffle.** `S`, "Shuffle" in the command palette, or `qobit --shuffle` for
  one run. Up Next goes into random order, a track you start from a list plays
  first with the rest shuffled, and *Add to queue* lands at a random place.
  The order is real: Up Next shows it and Previous goes back through what
  played. Turning shuffle off stops shuffling and leaves the queue as it is.
  The transport bar shows `[ ⤮ SHUFFLE ]`. Not available under Qobuz Connect.
- **Gapless playback**, on by default. `qobit --no-gapless` turns it off for
  one run, "Gapless playback" in the command palette saves the choice, and the
  transport bar shows `[ ⇉ GAPLESS ]` while it is on. In the last 30 seconds
  of a track qobit hands the next one to the same mpv, which has opened it by
  the time the track ends. When the sample rate or bit depth changes between
  two tracks, the output reopens for the new format: a short pause, because
  qobit never resamples. mpv is started with `--gapless-audio=weak`, so your
  own `mpv.conf` can't switch resampling on. Editing Up Next takes a lined-up
  track back out; any failure falls back to the old behaviour. Skipping still
  starts the track fresh, and Qobuz Connect playback is not gapless yet.

## [0.12.0] - 2026-11-17

### Added

- **Library at startup without waiting.** The Tracks, Albums, Artists and
  Playlists tabs show your library as it was last time, straight away, while
  qobit fetches it from Qobuz in the background. If anything changed, the tab
  updates and the cursor stays on the same item. Offline, the saved copy
  stays on screen. The copy is kept in `~/.cache/qobit/library`, next to
  the cover art; logging out erases it with the rest of the account's data.
- **`qobit clear-cache` clears the saved library too.** It deletes the cover
  art cache and the saved copy of the library; `--covers` or `--library`
  picks one. Both are fetched again when needed. It leaves the library
  alone while qobit is running (that copy would be saved again).

## [0.11.0] - 2026-11-17

### Added

- **Cover art in any terminal.** Terminals without the Kitty graphics
  protocol used to show every image as garbled characters. qobit now draws
  cover art there with coloured half blocks. Choose the mode with
  `qobit --images auto|kitty|blocks|off` or from the command palette
  ("Images", saved). `off` shows initials and downloads no covers, for
  slow machines.
- **Animations setting.** "Animations: turn off" in the command palette
  stops the wordmark's colour cycle and the bar meter, which cost CPU on
  slow machines such as a Raspberry Pi. `TEXTUAL_ANIMATIONS=none` turns
  them off too. See "Slow machines" in the usage guide.

### Fixed

- **Covers sent once.** Every time a cover was redrawn (moving the
  cursor in a grid redraws two cards), qobit re-encoded the image and sent it
  to the terminal again. Each image is now sent once and kept until it
  changes, and freed in the terminal when its page closes.
- **Playback list appears at once.** At startup it waited for your whole
  favourites library to download before showing a single track. It now
  shows the queue straight away and adds the hearts when the favourites
  arrive.
- **Less work while playing and moving around.** The playing track's bar
  meter, and every cursor move in a list, made qobit lay out the whole
  screen again; with a long queue that was most of its CPU time while
  playing (12% on a test machine, now 2%). A long Up Next now loads its rows as you
  scroll, as the other lists do, so moving the cursor costs half as much.
  Moving around the album and artist grids is faster too.
- **Faster tab switching.** Switching tabs restyled every row of the list
  being left and of the one being shown, and rebuilt the key bar at the
  bottom four or five times. Now only what changes is restyled, and the key
  bar is rebuilt once, when its keys change: a switch takes about half the
  time it did.
- **Much less memory.** Each grid card kept its full-size cover twice: after
  scrolling 300 albums qobit used 1.1 GB. Cards now keep a copy at their own
  size, shrunk off the UI thread, and fewer full covers stay in memory: about
  300 MB for the same scroll.
- **Faster startup.** The saved theme is now applied before the screen is
  built, instead of restyling every widget afterwards: about 30% less CPU
  time at startup.
- **Focus right after startup.** Switching tab in the first moment after
  launch could leave the keys going to the Playback list, hidden behind the
  new tab.
- **No traceback at startup** in terminals that don't report their cell
  size (the Linux console, for one): a warning from the image library was
  printed just before the interface appeared.

## [0.10.2] - 2026-11-17

### Fixed

- **Playlist page on short terminals.** At 28 rows the header left no room
  for a single track. It now shrinks its art like the album page does (and
  drops it when there's no room), so at least four tracks always show.
- A playlist's initials placeholder could land on the player bar's art
  instead of the playlist's own.
- **Readable panel hints.** The artist panels' "t or click the title to
  switch" hint was drawn in the faded border colour. It now uses the muted
  text colour, and the accent colour when the panel has focus, like the
  other tabs' sort and count hints. Every bordered panel now sets this
  colour.
- With the tab bar focused, the active tab is drawn like a selected row.
  In cyberdeck it was cyan text on pink, at 2.7:1 contrast; it now reads
  at 7:1.
- On an artist page's narrow album tiles, a HI-RES album showed
  "1959  ·" with the label cut off; HI-RES now moves below the year when
  it doesn't fit beside it.
- "Similar artists" spaces its count like every other panel title.

### Changed

- The README and the PyPI description say plainly what qobit is (a Qobuz
  client for the Linux and macOS terminal), so searches find it.

### Dependencies

- Security updates in the locked dependencies: anyio 4.16 (through httpx;
  a TLS host name encoding flaw), pillow 13.0, certifi 2026.10.23, msgpack
  1.2.4, and aiohttp 3.14.5 and multidict 7.2 (development tools only).
- textual 8.2.8 (Kitty extended-key parsing, a crash on clicks in the
  screen's padding), pyobjc 13.0 (macOS); ruff 0.17.5 for development.

## [0.10.1] - 2026-11-16

### Changed

- A ♥ marks a favourite album (after its title) and a followed artist (in
  the artist header's title), and follows changes from the `i` menu
  straight away.
- The player's second line shows the album's year: *Kind of Blue (1959)*.
- While a track plays, the `◢◤ qobit` mark in the top right slowly cycles
  through the theme's progress-bar colours (the accent and HI-RES colours in
  themes whose bar is a single colour).
- The progress bar's moving tip no longer shows a dark gap: the unfilled
  part of its cell takes the shaded track's colour.

### Fixed

- On a busy machine, opening an album from the Albums or Artists tab (or
  an artist page) could leave nothing focused, so keys did nothing until
  Tab. The track list now takes focus once the view has switched.
- **Acting on a filtered result.** While typing a `/` filter every key went
  into it (`i` too), and Enter played the highlighted track, replacing the
  queue. Enter now ends the typing and keeps the results, so `i`, `f` and
  Enter work on them; Escape clears the filter. The first result is
  selected as you type (lists and grids had no selection after filtering,
  so the menu found nothing). Tracks, Albums, Artists and Playlists.
- An album or playlist opened with no track selected: no cursor to see,
  and the `i` menu offered only the album's favourite (no *Go to artist*,
  *Play next*…). The first track, or the one playing, is now selected. On
  an album card, `i` also offers *Go to artist*.
- *Play radio* from the `i` menu on the track that's playing restarted it;
  now only Up Next becomes the station (as with `R`). On the track restored
  at startup it resumes where you left off.
- **qobit closed when a track had no stream at all.** For some tracks
  Qobuz answers with only a restriction (`SampleRestrictedByRightHolders`:
  not even a preview), and reaching one (Next, Enter, autoplay) crashed
  the app. Such a track is now skipped with *Not available on Qobuz*, like
  a preview-only one (or playback stops if nothing follows it); `qobit
  play` reports it too.
- **Albums on short terminals.** An album opened from the Artists tab, Search
  or `g` showed its art and the artist header but not a single track at 32
  rows. The tracklist now always keeps at least 8 rows (and half the album
  panel); the art shrinks to fit, the artist header steps aside when there's
  no room for it, and on very short terminals the art goes and the
  tracklist splits into two columns.
- In an album or playlist, Tab (or a click on the cover or album info) left
  focus on the header, with no sign of it, and the tracks needed one more
  Tab. Tab and clicks now go straight to the track list.
- Artist pages showed the biography's HTML (`<p>…`) as text; it's plain
  text now, as in the Now Playing panel. Text from Qobuz HTML (bios,
  album and playlist descriptions) no longer gets stray double spaces or a
  space before punctuation where a tag was.

## [0.10.0] - 2026-11-16

### Changed

- **Nothing to compile on Linux.** Media keys and the desktop's now-playing
  widget (MPRIS) now use dbus-fast instead of pydbus and PyGObject, so
  installing qobit needs only mpv: no compiler, headers or `-dev` packages.
  A second qobit running at the same time shows up as its own player
  instead of having no media keys.

## [0.9.0] - 2026-11-16

### Added

- qobit is published on PyPI: `uv tool install qobit` (or `pipx install
  qobit`). The README has macOS install steps.
- CI runs the tests on Python 3.11 and 3.13, on Linux and macOS.

## [0.8.0] - 2026-11-16

### Security

- **Email/password login removed.** It saved the password in plaintext in
  `config.json`, sent it in a request URL, and couldn't stream anyway. qobit
  logs in through the browser only (`qobit auth`); a password an older qobit
  saved is deleted at startup, with a notice. `QOBUZ_EMAIL` /
  `QOBUZ_PASSWORD` are no longer read.
- **Text from Qobuz can no longer drive the terminal.** Titles, names and
  playlist descriptions (public playlists are written by other users) passed
  escape sequences straight through: one could rewrite the clipboard (OSC
  52) or redraw the screen. Control characters are now stripped from every
  API response, error message and saved state file.
- The session token travels in a request header, never in the URL (URLs end
  up in logs).
- The stream URL (signed, naming the account) no longer appears in mpv's
  command line, which any local user can read: mpv reads it from a private
  playlist file, deleted when playback stops.
- A crash screen no longer shows the session token: Textual prints local
  variables there, and credentials are now masked in them.
- The cover-art cache (a record of what was played) is readable only by
  you.
- **The session token lives in the system keyring**: the macOS Keychain, or
  the Secret Service on Linux (GNOME Keyring, KWallet). `config.json` keeps
  only a marker. Without a keyring (headless Linux, SSH) the token stays in
  `config.json`; `QOBIT_KEYRING=0` forces that. A token already in the file
  moves to the keyring at the next start. `qobit auth status` says where it
  is.
- `config.json` (the session token) and the files in `~/.local/share/qobit`
  are written 0600 from the first byte, in 0700 directories. Before, the
  token was briefly world-readable on every settings change (and stayed so
  if the chmod failed), and the mpv control socket's folder relied on the
  umask. Existing files and folders are tightened at startup.
- The browser login's local server listens on loopback only, on a port
  picked as it binds, and takes one code. Each login's return address has a
  random path, and a code is taken only there, so another web page or
  program can't log qobit into a different account.

### Added

- **Log in inside qobit.** Without a session, or when it expires, qobit opens
  a login dialog instead of failing in every tab: the link (copy with `c`,
  open with `o`, or click it), a field for the address the browser ends on,
  and your queue kept. `^p` → *Log in to Qobuz* / *Log out of Qobuz*.
- `qobit auth status` (who is logged in, and whether the session still
  works).
- **Logging out erases the account from this machine** (`qobit logout`, or
  `^p` → *Log out of Qobuz*): the session, queue and history, last track,
  mixes and cached covers, and in a running qobit the library and queue on
  screen. Settings stay; `qobit logout --all` removes them too. A different
  account logging in after an expiry gets a clean slate as well.
- Log in from a machine without a browser (SSH, a headless box): open the
  link anywhere, then paste the address the browser ends on into
  `qobit auth`. The login now waits 5 minutes instead of 2.

### Changed

- `qobit auth` always prints the login link, so it can be opened by hand
  when the browser doesn't come up. Without a display (SSH, a console) qobit
  no longer tries to open a browser, which could start a text browser in the
  terminal.
- A login code Qobuz rejects (expired, already used) gets a plain message.

## [0.7.0] - 2026-11-16

### Added

- A **bit-perfect lamp** on the transport bar and the Now Playing panel: a
  couple of seconds into each track qobit checks mpv's output and, on Linux,
  the rate and format the DAC actually runs at (catching a sound server that
  resamples behind mpv). `◉ BIT-PERFECT`, or what differs:
  `◌ RESAMPLED 96 → 48 kHz`, `◌ REDUCED 24 → 16-BIT`, `◌ SOFTWARE VOLUME 80%`.
- `HI-RES` tag for streams and albums above CD quality, in its own theme
  colour (magenta in cyberdeck, blue in Ayu), on the transport bar, the Now
  Playing panel, album cards and the album page.
- The playing track's `▶` becomes a small animated bar meter while it plays.
- Covers show a tile with the artist's or album's initials while they load,
  or when there's no image.
- A `◢◤ qobit` wordmark at the right of the tab bar (from 102 columns).

### Changed

- Pages (an album, artist or playlist opened from Search, `g`, `i` or Similar)
  keep the tab bar, with the tab they came from lit, and show the whole trail
  (`Search › Mogwai › Mr. Beast`) instead of one `← back` label. Click any
  part to go back there; Escape goes up one level. The Albums and Artists
  tabs' inline details use the same trail.
- The progress bar moves smoothly (an eighth of a cell at a time); in
  cyberdeck it's a cyan-to-magenta gradient.
- Panel titles are uppercase readouts (`FAVOURITE ALBUMS`), like the queue's
  section dividers.
- Library grids fit more tiles: 3 columns at 120 terminal columns (was 2).
- The cyberdeck theme has three colour roles: cyan chrome, neon pink for
  signal (HI-RES, hearts) and amber LED readouts (the transport's status and
  time, the footer keys). Muted text is tinted cyan and easier to read
  (10.7:1), the selection tint is lighter so pink and muted text stay
  readable on it, and the violet secondary is brighter.
- The Now Playing panel's genre/label line, the playing row's album line, the
  playlist card date and the mix track count are easier to read in every theme.
- Track rows line up: the title and the album line start in the same column
  in every list, and the playing row's ▶/⏸ takes the place of its number or
  `♪` instead of pushing the row to the right.
- Long album names end in `…` instead of wrapping onto the next row.
- The footer shows only the keys that apply: `esc Back` when there's somewhere
  to go back to, no Sort/Filter while an album or artist is open, and `-`/`=`
  only once software volume is turned on.
- When a track ends, a list's cursor follows playback only if it was on the
  playing track, so browsing a list (or Up Next) no longer loses your place.
- The Now Playing panel shows the stream's resolution, and its lines no longer
  wrap.
- Resolution is written the same way everywhere (`24-BIT · 96 kHz`), and
  separators are `  ·  ` throughout.
- Album and playlist panels are titled "Album" / "Playlist" instead of repeating
  the name shown right below.
- The Playback list updates in place when a track ends or the queue changes,
  instead of being cleared and rebuilt: no flicker, and the scroll position
  stays.
- Empty, loading and error states look the same in every tab; tabs show
  "loading…" while they fetch.

### Fixed

- With the theme background off, the cursor on the playing track lost its
  highlight and its text turned the selection's text colour on the bare
  terminal (black on black in `ansi-dark`). The playing row now highlights
  like any other.
- Readability across all 25 themes (checked automatically in both background
  modes): unfocused panel titles, inactive tabs, secondary text, the album
  page's details, the transport's album line, the bit-perfect lamp and the
  footer were too faint in many themes (down to 1.0:1: Solarized Light's
  footer, and light themes' muted text with the background off). Secondary
  text now uses a colour qobit checks per theme, and everything reads at
  4.5:1 or better except a few of the themes' own colours.
- On the Mixes tab, Space opened the focused mix instead of pausing (and
  `space Pause` was missing from the footer there). `Enter` opens a mix.
- On short terminals the Now Playing panel's cover pushed the resolution and
  the bit-perfect lamp out of view: the cover now shrinks to fit, and hides
  when there's no room for it.
- After switching theme, the bit-perfect lamp and the transport readout kept
  the previous theme's colours until the next track.
- A selected row, card or menu option is readable in every theme. Its text
  was the accent on an accent tint, unreadable in most of Textual's themes
  and every light one, and in ANSI themes (`ansi-dark`) the same colour as
  its background. qobit now picks the selection's text colour per theme by
  measuring contrast (ANSI themes use their own cursor colours), and
  everything on a selection, hearts and HI-RES included, takes it. The
  playing row, hearts and HI-RES also stay readable on light themes.
- Backgrounds are consistent: no page section paints one (the album and
  artist headers, the footer, the search box, scrollbar tracks and cover
  placeholders did, some only with the theme background on, some even with
  it off). Panels are set apart by their borders; the only fills left are the
  selection, the now-playing row and the `i`/`g` menu, the same in both modes.
- `1`–`7` on an album, artist or playlist page switched the hidden tab under
  it, so Escape then landed somewhere unexpected. They now close the page and
  switch tab.
- The playlist page's panel sat one column inside the transport bar.
- Album names, artist names and titles containing `[…]` lost that part (it was
  read as markup) in list rows, breadcrumbs and the Now Playing panel.

## [0.6.0] - 2026-11-15

### Added

- The artist page shows **Your favourites**: your favourite tracks by that
  artist, next to Top Tracks. Side by side on wide terminals; on narrower ones
  they share a panel; `t` or a click on the panel title switches. Hidden when
  you have none.
- Similar artists on the artist page: in the Albums & EPs panel (`t` or a
  click on the panel title switches), and as a short list beside the bio from
  128 columns. `♥` marks artists you follow.
- The `i` and `g` menus wrap around: up on the first option goes to the last.

### Changed

- Panels with two views name both in the title, the shown one underlined;
  click a name, or press `t`, to switch.

### Fixed

- Opening the `i` or `g` menu (or the command palette) shifted the whole
  screen behind it one column to the right, and hid every image behind it.
  The page behind a menu is no longer dimmed.
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

[0.6.0]: https://github.com/pierdom/qobit/releases/tag/v0.6.0
[0.5.0]: https://github.com/pierdom/qobit/releases/tag/v0.5.0
[0.4.0]: https://github.com/pierdom/qobit/releases/tag/v0.4.0
[0.3.0]: https://github.com/pierdom/qobit/releases/tag/v0.3.0
[0.2.2]: https://github.com/pierdom/qobit/releases/tag/v0.2.2
[0.2.1]: https://github.com/pierdom/qobit/releases/tag/v0.2.1
[0.2.0]: https://github.com/pierdom/qobit/releases/tag/v0.2.0
[0.1.1]: https://github.com/pierdom/qobit/releases/tag/v0.1.1
[0.1.0]: https://github.com/pierdom/qobit/releases/tag/v0.1.0
