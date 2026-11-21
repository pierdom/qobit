# Audio setup: getting bit-perfect output

qobit's main rule is that the DAC receives exactly the bits from the FLAC file,
with no resampling and no DSP. mpv plays the stream. How the samples reach the
DAC depends on the output device you pick with `qobit set-device` (you can
change it any time; `qobit devices` lists them). `qobit doctor` checks the
setup (see [When it isn't bit-perfect](#when-it-isnt-bit-perfect)).

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

## When it isn't bit-perfect

`qobit doctor` checks the machine and prints what to change. It changes
nothing, needs no login, and exits with 1 if it found a problem, so a script
can use it.

**What it checks**

- *mpv*: it is installed, and recent enough for gapless (0.34).
- *Output*: the saved device is still listed and is of a bit-perfect kind
  (`alsa/hw:`, `coreaudio/`); software volume is off or at 100%.
- *mpv config*: qobit starts mpv with your `mpv.conf`, so a volume, sample
  rate, sample format, channel layout, ReplayGain or mute there breaks the
  signal. Options qobit overrides anyway are listed as ignored.
- *DAC and sound server* (Linux): the DAC's rates and sample sizes. For
  `alsa/hw:`, whether you can open the card and whether another program is
  playing on it. For PipeWire or PulseAudio, whether the graph follows each
  track's rate, with the drop-in for your DAC's rates to paste (option A).
  On macOS it warns if the saved device is not a CoreAudio one.
- *Terminal*: true colour, Kitty graphics, tmux passthrough.

**Reading the output**

Each line starts with a mark: `✓` fine, `·` for your information, `!` a
warning (the output may not be bit-perfect), `✗` a problem (it won't be).
Warnings and problems are repeated at the end under "To fix", numbered, with
the commands or config to paste, followed by a count. This is a PipeWire clock
locked to 48 kHz in front of a 384 kHz DAC:

```
DAC and sound server
  · DAC: USB-Audio - FiiO K13 R2R
      16/32-bit 44.1, 48, 88.2, 96, 176.4, 192, 352.8, 384 kHz
  ! PipeWire allows 48 kHz
      Tracks at 44.1, 88.2, 96, 176.4, 192, 352.8, 384 kHz are resampled before they reach your DAC.

To fix
  1. PipeWire allows 48 kHz
     Create ~/.config/pipewire/pipewire.conf.d/10-allowed-rates.conf with:

         context.properties = {
             default.clock.allowed-rates = [ 44100 48000 88200 96000 176400 192000 352800 384000 ]
         }

     then: systemctl --user restart pipewire pipewire-pulse wireplumber

0 problems, 1 warning.
```

If it says the DAC is "in use by mpv", that may be qobit itself: stop playback
and run it again. The output names your `mpv.conf` lines, the sound card and
the program using it, so read it before pasting it into an issue.

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

In the last 30 seconds of a track qobit adds the next stream to the mpv that
is already playing. mpv opens it early and, when the format matches, keeps the
output device open across the boundary.

- **A new sample rate or bit depth** makes mpv reopen the device: a short
  pause. The alternative is resampling the second track to the first one's
  rate, which breaks the bit-perfect rule. qobit starts mpv with
  `--gapless-audio=weak`, so your own `mpv.conf` can't switch it on.
- **Skipping** (next, previous, picking a row, media keys) starts the track
  fresh. Editing Up Next takes a lined-up track back out.
- **Qobuz Connect** playback is not gapless yet: the server decides when a
  track ends.
- If the next stream can't be lined up (offline, preview-only track, expired
  link), the next track starts when this one ends, as before.

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
