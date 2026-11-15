# PipeWire rate-following config (Linux)

This directory has an optional PipeWire drop-in for qobit's recommended Linux
setup. With it, PipeWire switches your DAC to each track's sample rate instead
of resampling everything to one fixed rate.

The DAC stays a normal system device that other apps can use, your system
volume keeps working, and streaming FLAC reaches the DAC unchanged. The
[audio setup guide](../../docs/audio.md) compares this with exclusive
`alsa/hw:` output (option B).

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

The included file lists the rates of a FiiO K13 R2R (44.1k to 384k). Check
which rates your DAC supports and edit the list to match:

```bash
cat /proc/asound/card*/stream0      # see the "Rates:" lines
```

Keep only the rates your hardware lists. DSD rates are left out because qobit
only streams PCM FLAC.

## Verify it works

While a track is playing, check the rate the DAC is running at. It should match
the track, not a fixed 48000:

```bash
cat /proc/asound/card*/pcm0p/sub0/hw_params      # look for "rate:"
```

Example, playing a 24/192 FLAC:

```
format: S32_LE
rate: 192000 (192000/1)
```

You can also check that PipeWire is allowed to switch rates:

```bash
pw-metadata -n settings | grep allowed-rates
# clock.allowed-rates value:'[ 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000 ]'
```

## Keep in mind

- The DAC gets the file's exact bits only for ≤24-bit content, at 100% volume,
  with no other app playing. If you lower the volume, another app plays sound,
  or the source is 32-bit integer, PipeWire changes the samples. Streaming FLAC
  at full volume reaches the DAC unchanged.
- Keep the volume at 100% if you want unchanged output.
- To guarantee unchanged output all the time, use a raw
  `alsa/hw:CARD=...,DEV=0` device instead. The DAC then can't be shared with
  other apps, and system volume no longer applies.
