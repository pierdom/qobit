# qobit — developer guide

## Project shape

Bit-perfect Qobuz TUI. Python 3.11+, Textual for the UI, mpv for audio,
packaged with uv. The non-negotiable invariant: the DAC receives exactly the
bits from the FLAC file — no resampling, no DSP.

## Module layout

```
src/qobit/
├── __init__.py          version string
├── __main__.py          CLI entry point (Phase 1); will launch TUI in Phase 2
├── config.py            credentials + device config (env → file → prompt)
├── store.py             JSON persistence for runtime state
├── qobuz/
│   ├── client.py        QobuzClient — lifted from pierdom/qobuz-mcp
│   └── models.py        typed dataclasses (Track, Album, Artist, StreamUrl)
├── audio/
│   ├── device.py        enumerate mpv audio devices
│   ├── player.py        mpv subprocess wrapper with bit-perfect flags
│   └── verify.py        post-play rate/format verification via mpv IPC
└── ui/                  Textual TUI (Phase 2+)
```

## Bit-perfect strategy

### Linux

Use `alsa/hw:CARD=<name>,DEV=0` as the audio device. The ALSA `hw:` plugin
gives direct hardware access — no PulseAudio, no PipeWire mixing, no dmix
resampler. mpv will error rather than resample if the device doesn't support
the track's sample rate, which is the desired behaviour.

Verify via mpv IPC: `audio-params/samplerate` must match the stream URL's
`sampling_rate` field.

Note for PipeWire users: PipeWire may intercept `hw:` writes on modern
distros. Workaround (Phase 5): implement D-Bus `org.freedesktop.ReserveDevice1`
to pre-empt PipeWire, same approach as vicrodh/qbz.

### macOS

Use `coreaudio/<device-uid>` plus `--audio-exclusive=yes`. This engages Core
Audio hog mode, preventing the system mixer from touching the signal. Core
Audio in exclusive mode switches the device sample rate to match the source.

## Qobuz API layer

`src/qobit/qobuz/client.py` is copied verbatim from `pierdom/qobuz-mcp`
(with async context manager support added). It reverse-engineers app_id and
app_secret from Qobuz's web player bundle on first login and caches the
working values.

Streaming URL quality fallback order: FLAC_24_192 → FLAC_24_96 → FLAC_CD.

Streaming URLs expire in ~10 minutes. Re-fetch on track start, not on app
start.

## Image protocol

Kitty graphics protocol only (no iTerm2 IIP, no sixel). Targets: kitty,
Ghostty, iTerm2 3.5+. Implemented via `textual-image` in Phase 3.

## Reference repos

- `pierdom/picast` — audio player TUI template (Rich rendering model; note:
  picast uses Raw Rich not Textual — qobit uses Textual instead)
- `pierdom/tuidash` — Textual patterns: `@work(thread=True)` for I/O,
  `reactive` + `watch_*` for state, `DEFAULT_CSS`, `ContentSwitcher`
- `vicrodh/qbz` — bit-perfect audio reference (Rust, but the ALSA/CoreAudio
  strategy maps directly to mpv flags)

## Config paths

```
~/.config/qobit/config.json     credentials, audio_device, theme
~/.local/share/qobit/           runtime state (mpv.sock, playback positions)
```

## Development

```bash
uv sync --group dev
uv run qobit auth
uv run qobit devices
uv run qobit play "test query"
uv run ruff check .
uv run ruff format .
```

## Phases

1. CLI slice: auth → search → stream URL → bit-perfect play → verify ✓
2. Textual TUI: search screen, album screen, transport bar
3. Album art via textual-image (Kitty protocol)
4. Library: artist/album browser, playlists, history
5. MPRIS (Linux), macOS MediaRemote, PipeWire ReserveDevice1
6. Gapless, ReplayGain, CMAF fallback
