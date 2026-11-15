# Audio setup: getting bit-perfect output

qobit's one non-negotiable rule: the DAC receives exactly the bits from the FLAC
file — no resampling, no DSP. mpv plays the stream; *how* the samples reach the
DAC depends on the output device you pick with `qobit set-device` (change it any
time; `qobit devices` lists them).

| Option | DAC stays a system device | Volume | Sample rate | Bit-perfect |
|---|---|---|---|---|
| **A. PipeWire rate-following** *(recommended on Linux)* | ✅ | ✅ system volume | follows each track | ✅ for ≤24-bit at 100% volume |
| **B. Exclusive `alsa/hw:` / `coreaudio:`** | ❌ while qobit plays | the DAC's knob | follows each track | ✅ always |
| **C. Plain PipeWire/Pulse** | ✅ | ✅ system volume | resampled to a fixed clock | ❌ |

**Which one?** A dedicated DAC with a volume knob and zero compromise → **B**.
An everyday desktop where the DAC is also the system output → **A** (audibly
identical to B for streaming FLAC). **C** only if you can't change the
PipeWire config.

## A — PipeWire rate-following

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

## B — Exclusive hardware

Pick a device marked `♪` in `qobit devices` (they're listed first):

- **Linux:** a raw `alsa/hw:CARD=<name>,DEV=0`. It bypasses PipeWire/Pulse, so
  mpv negotiates the file's native rate with the DAC. mpv itself never lists raw
  `hw:` devices; qobit adds them.
- **macOS:** `coreaudio/<device-uid>` — qobit turns on Core Audio exclusive
  ("hog") mode.

Nothing can touch the stream, but the DAC is exclusive while qobit plays, and
volume comes from the DAC itself. qobit's own software volume is off by default
and should stay off here (`Ctrl+P` → *Software volume* — below 100% it changes
the samples).

## C — Plain PipeWire/Pulse

Option A without the rate drop-in: everything is resampled to one clock
(often 48 kHz). Configure A instead if you can.

## Quality

qobit asks for the best format your subscription allows and falls back in this
order:

| Format | |
|---|---|
| `FLAC_24_192` | 24-bit / up to 192 kHz (default) |
| `FLAC_24_96` | 24-bit / up to 96 kHz |
| `FLAC_CD` | 16-bit / 44.1 kHz |

(`qobit play --quality MP3_320` exists for the CLI.) Tracks your account can't
stream come back from Qobuz as a 30-second MP3 preview; qobit never plays those
— it shows "Not streamable (preview only)" and skips to the next track.

## Verifying

After a track starts, qobit reads mpv's actual output format over IPC and
compares it with the stream: `qobit play` prints a `✓ 24/192 bit-perfect` line
when they match. The TUI shows the stream's resolution on the transport bar
(`[ 24-BIT · 192 kHz ]`), and Qobuz Connect reports the same format to the apps.
