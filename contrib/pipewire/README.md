# PipeWire rate-following config (Linux)

This directory holds an optional PipeWire drop-in that makes qobit's
**recommended** Linux audio path work: PipeWire reclocks your DAC to match each
track's sample rate instead of resampling everything to one fixed clock.

This is the convenient sweet spot — the DAC stays a normal, shareable system
device, your system volume keeps working, and playback is bit-transparent for
streaming FLAC. See the main [README "Audio device setup"](../../README.md#audio-device-setup)
for how this compares to exclusive `alsa/hw:` (Option B) output.

## Install

```bash
mkdir -p ~/.config/pipewire/pipewire.conf.d
cp 10-allowed-rates.conf ~/.config/pipewire/pipewire.conf.d/
systemctl --user restart pipewire pipewire-pulse wireplumber
```

Then point qobit at the ordinary `pipewire/...` device for your DAC:

```bash
qobit set-device      # pick the pipewire/... entry for your DAC
```

## Tailor it to your DAC

The shipped file lists the rate ladder of a FiiO K13 R2R (44.1k → 384k).
Discover your own DAC's supported rates and trim the list to match:

```bash
cat /proc/asound/card*/stream0      # see the "Rates:" lines
```

Keep only rates your hardware actually advertises. (DSD rates are omitted —
qobit streams PCM FLAC only.)

## Verify it works

While a track is playing, check the rate the DAC hardware is actually opened at
— it should match the track, not a fixed 48000:

```bash
cat /proc/asound/card*/pcm0p/sub0/hw_params      # look for "rate:"
```

Example, playing a 24/192 FLAC:

```
format: S32_LE
rate: 192000 (192000/1)
```

You can also confirm PipeWire is now allowed to switch:

```bash
pw-metadata -n settings | grep allowed-rates
# clock.allowed-rates value:'[ 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000 ]'
```

## Keep in mind

- **Bit-transparent, not bit-perfect-by-letter.** Identical bits reach the DAC
  for ≤24-bit content at **100% volume** with nothing else playing. Lower the
  volume, mix in another app, or play a 32-bit-int source and PipeWire applies
  gain/conversion. For streaming FLAC at full volume, you get the file's bits.
- **Keep volume at 100%** when transparency matters.
- For a hard, always-on guarantee (at the cost of the DAC going exclusive and
  losing software volume), use a raw `alsa/hw:CARD=...,DEV=0` device instead.
