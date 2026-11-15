# Audio setup: getting bit-perfect output

qobit's main rule is that the DAC receives exactly the bits from the FLAC file,
with no resampling and no DSP. mpv plays the stream. How the samples reach the
DAC depends on the output device you pick with `qobit set-device` (you can
change it any time; `qobit devices` lists them).

| Option | DAC stays a system device | Volume | Sample rate | Bit-perfect |
|---|---|---|---|---|
| **A. PipeWire rate-following** *(recommended on Linux)* | ✅ | ✅ system volume | follows each track | ✅ for ≤24-bit at 100% volume |
| **B. Exclusive `alsa/hw:` / `coreaudio:`** | ❌ while qobit plays | the DAC's knob | follows each track | ✅ always |
| **C. Plain PipeWire/Pulse** | ✅ | ✅ system volume | resampled to a fixed clock | ❌ |

Which one to use: **B** for a dedicated DAC with its own volume knob. **A** for
an everyday desktop where the DAC is also the system output; for streaming FLAC
it sounds the same as B. **C** only if you can't change the PipeWire config.

## A. PipeWire rate-following

Use the ordinary `pipewire/...` device for your DAC and let PipeWire reclock it
to each track's rate instead of resampling. Most distros pin PipeWire to one
rate; unlock the rates your DAC supports with the drop-in in
[`contrib/pipewire`](../contrib/pipewire/README.md):

```bash
mkdir -p ~/.config/pipewire/pipewire.conf.d
cp contrib/pipewire/10-allowed-rates.conf ~/.config/pipewire/pipewire.conf.d/
systemctl --user restart pipewire pipewire-pulse wireplumber
```

Check during playback that `rate:` matches the track:

```bash
cat /proc/asound/card*/pcm0p/sub0/hw_params
```

PipeWire's float32 pipeline represents 24-bit samples exactly, so this is
bit-transparent while qobit is the only app playing and volume is at 100%.

## B. Exclusive hardware

Pick a device marked `♪` in `qobit devices` (they're listed first):

- **Linux:** a raw `alsa/hw:CARD=<name>,DEV=0`. It bypasses PipeWire/Pulse, so
  mpv negotiates the file's native rate with the DAC. mpv itself never lists raw
  `hw:` devices; qobit adds them.
- **macOS:** `coreaudio/<device-uid>`. qobit turns on Core Audio exclusive
  ("hog") mode.

No other software can change the stream. The trade-off: no other app can use
the DAC while qobit plays, and you set the volume on the DAC itself. qobit's
software volume (`Ctrl+P` → *Software volume*) is off by default; keep it off
here, because below 100% it changes the samples.

## C. Plain PipeWire/Pulse

Option A without the rate drop-in. Everything is resampled to one clock
(often 48 kHz). Configure A instead if you can.

## Quality

qobit asks for the best format your subscription allows and falls back in this
order:

| Format | |
|---|---|
| `FLAC_24_192` | 24-bit / up to 192 kHz (default) |
| `FLAC_24_96` | 24-bit / up to 96 kHz |
| `FLAC_CD` | 16-bit / 44.1 kHz |

The CLI also accepts `qobit play --quality MP3_320`. For tracks your account
can't stream, Qobuz returns a 30-second MP3 preview. qobit doesn't play these:
it shows "Not streamable (preview only)" and skips to the next track.

## Verifying

`qobit play` checks the output: after the track starts, it reads mpv's actual
output format over IPC, compares it with the stream, and prints
`✓ 24/192 bit-perfect` when they match. The TUI doesn't run this check. It shows
the stream's format on the transport bar (`[ 24-BIT · 192 kHz ]`), and Qobuz
Connect reports the same format to the apps.
