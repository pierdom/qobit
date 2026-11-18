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

## Gapless playback

Tracks that follow each other play without a break (albums, playlists, live
records). In the last 30 seconds of a track qobit asks Qobuz for the next
stream and adds it to the mpv that is already playing; mpv opens it early and,
when the format matches, keeps the output device open across the boundary.

- **A new sample rate or bit depth** (a 44.1 kHz track followed by a 96 kHz
  one) makes mpv reopen the device for the new format. That is a short pause,
  and it is on purpose: the other way is to resample the second track to the
  first one's rate, which breaks the bit-perfect rule. qobit starts mpv with
  `--gapless-audio=weak` so that your own `mpv.conf` can't switch that on.
- **Skipping** (next, previous, picking a row, the media keys) starts the
  track fresh, as before. Changing Up Next while a track is lined up takes it
  back out of mpv.
- **Qobuz Connect** playback is not gapless yet: the server decides there when
  a track ends.
- A stream that can't be lined up (offline, a preview-only track, an expired
  link) simply falls back to starting the next track when this one ends.

## Verifying

`qobit play` checks the output: after the track starts, it reads mpv's actual
output format over IPC, compares it with the stream, and prints
`✓ 24/192 bit-perfect` when they match.

The TUI runs a fuller check a couple of seconds into every track and shows the
verdict on the transport bar and the Now Playing panel:

| Lamp | Meaning |
|------|---------|
| `◉ BIT-PERFECT` | mpv outputs the file's rate and bit depth, and (Linux) the DAC runs at them too |
| `◌ RESAMPLED 96 → 48 kHz` | mpv, or the sound server behind it, changes the sample rate |
| `◌ REDUCED 24 → 16-BIT` | fewer bits reach the output than the file has |
| `◌ SOFTWARE VOLUME 80%` | software volume is on and below 100%: that's DSP |

On Linux the DAC side comes from `/proc/asound/card*/pcm*p/sub*/hw_params`, the
rate and format the hardware is actually opened at. That catches PipeWire
resampling when its graph didn't follow the track's rate (see the
`allowed-rates` drop-in above), which mpv alone can't see. If several devices
are playing at once, or on macOS, the lamp goes by mpv alone. No lamp means the
check couldn't run (mpv didn't answer). Qobuz Connect reports the stream's
format to the apps.
