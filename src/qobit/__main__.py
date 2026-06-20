"""qobit CLI — Phase 1 entry point.

Usage:
  qobit play <query> [--quality QUALITY]
  qobit devices
  qobit set-device
  qobit auth
  qobit clear-cache

Running qobit with no arguments launches the TUI.
"""

import argparse
import asyncio
import sys
import time

from . import __version__
from .audio.device import list_devices
from .audio.player import MpvPlayer
from .audio.verify import check_bit_perfect
from .config import (
    get_audio_device,
    get_credentials,
    get_oauth_session,
    prompt_and_save_credentials,
    save_oauth_session,
    set_audio_device,
)
from .qobuz.client import QobuzClient, QobuzError
from .qobuz.models import StreamUrl, Track

_QUALITY_FALLBACK = ["FLAC_24_192", "FLAC_24_96", "FLAC_CD"]


async def _authenticate(client: QobuzClient) -> bool:
    """Try saved OAuth session, then browser OAuth, then email/password fallback."""
    app_id, token, secrets = get_oauth_session()
    if app_id and token and secrets:
        print("Resuming session...", end=" ", flush=True)
        client.restore_session(app_id, token, secrets)
        print("OK")
        return True

    print("Authenticating via browser OAuth...")
    try:
        await client.login_oauth()
        save_oauth_session(
            client._app_id or "", client.user_auth_token or "", client._secret_candidates
        )
        return True
    except QobuzError as e:
        print(f"OAuth failed: {e}", file=sys.stderr)

    # Last resort: email/password (streaming won't work but search will)
    email, password = get_credentials()
    if not email:
        email, password = prompt_and_save_credentials()
    if not email:
        return False
    try:
        await client.login(email, password)
        return True
    except QobuzError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return False


async def cmd_play(args: argparse.Namespace) -> int:
    device = get_audio_device()
    if not device:
        print(
            "No audio device configured. Run: qobit set-device\n"
            "Falling back to mpv auto-select (rate verification may be inaccurate).\n",
            file=sys.stderr,
        )

    preferred = args.quality

    async with QobuzClient() as client:
        if not await _authenticate(client):
            print("Authentication failed — exiting.", file=sys.stderr)
            return 1

        print(f"Searching: {args.query!r}...", end=" ", flush=True)
        results = await client.search(args.query, type="tracks", limit=5)
        tracks_raw = results.get("tracks", {}).get("items", [])
        if not tracks_raw:
            print("no results.", file=sys.stderr)
            return 1

        track = Track.from_api(tracks_raw[0])
        print(f"found: {track.artist} — {track.display_title} ({track.duration_str})")

        # Try preferred quality, then fall back down the chain.
        fallbacks = [preferred] + [q for q in _QUALITY_FALLBACK if q != preferred]
        stream: StreamUrl | None = None
        for quality in fallbacks:
            print(f"Resolving stream URL ({quality})...", end=" ", flush=True)
            try:
                url_data = await client.get_streaming_url(str(track.id), quality)
                stream = StreamUrl.from_api(url_data)
                print(f"OK ({stream.quality_label} kHz)")
                break
            except QobuzError as e:
                print(f"unavailable ({e})")

        if stream is None:
            print("No playable stream found.", file=sys.stderr)
            return 1

        player = MpvPlayer(audio_device=device)
        device_label = device or "auto"
        print(f"Playing via {device_label}...")
        player.play(stream.url)

        print("Verifying output...", end=" ", flush=True)
        report = await check_bit_perfect(player, stream.sampling_rate, stream.bit_depth or 16)
        print(report.summary())

        if not report.bit_perfect and not report.error:
            print(
                "\nHint: for bit-perfect output on Linux use alsa/hw:CARD=<name>,DEV=0\n"
                "      on macOS use coreaudio/<device>  (run: qobit set-device)",
                file=sys.stderr,
            )

        print("\nPress Ctrl-C to stop.")
        try:
            while player.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            player.stop()

    return 0


def cmd_devices(_args: argparse.Namespace) -> int:
    try:
        devices = list_devices()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    if not devices:
        print("No audio devices found.", file=sys.stderr)
        return 1

    current = get_audio_device()
    print(f"\n{'DEVICE ID':<55}  DESCRIPTION")
    print("-" * 90)
    for d in devices:
        marker = " *" if d.id == current else ""
        print(f"{d.id:<55}  {d.description}{marker}")
    print()
    if current:
        print(f"Active: {current}")
    else:
        print("No device configured — run: qobit set-device")
    return 0


def cmd_set_device(_args: argparse.Namespace) -> int:
    try:
        devices = list_devices()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    if not devices:
        print("No audio devices found.", file=sys.stderr)
        return 1

    current = get_audio_device()
    print("\nAvailable audio devices:\n")
    for i, d in enumerate(devices):
        marker = "  (current)" if d.id == current else ""
        print(f"  [{i:2}]  {d.description:<45}  {d.id}{marker}")

    print()
    raw = input("Select device number (Enter to cancel): ").strip()
    if not raw:
        return 0
    try:
        device = devices[int(raw)]
    except (ValueError, IndexError):
        print("Invalid selection.", file=sys.stderr)
        return 1

    set_audio_device(device.id)
    print(f"\nSaved: {device.id}")
    print("Tip: prefer alsa/hw:... (Linux) or coreaudio/... (macOS) for bit-perfect output.")
    return 0


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def cmd_clear_cache(_args: argparse.Namespace) -> int:
    from .ui._images import clear_disk_cache

    path, removed, freed = clear_disk_cache()
    if path is None:
        print("Cache directory unavailable — nothing to clear.", file=sys.stderr)
        return 1
    if removed == 0:
        print(f"Cache already empty: {path}")
    else:
        print(f"Cleared {removed} cached image(s) ({_human_bytes(freed)}) from {path}")
    return 0


async def cmd_auth(_args: argparse.Namespace) -> int:
    async with QobuzClient() as client:
        try:
            await client.login_oauth()
        except QobuzError as e:
            print(f"OAuth failed: {e}", file=sys.stderr)
            return 1

        save_oauth_session(
            client._app_id or "", client.user_auth_token or "", client._secret_candidates
        )
        print("Authentication successful. Session saved.")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="qobit",
        description="Bit-perfect Qobuz client",
    )
    parser.add_argument("--version", action="version", version=f"qobit {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_play = sub.add_parser("play", help="Search and play a track")
    p_play.add_argument("query", help="Search query")
    p_play.add_argument(
        "--quality",
        "-q",
        choices=["FLAC_24_192", "FLAC_24_96", "FLAC_CD", "MP3_320"],
        default="FLAC_24_192",
        metavar="QUALITY",
        help="Preferred quality: FLAC_24_192 (default), FLAC_24_96, FLAC_CD, MP3_320",
    )

    sub.add_parser("devices", help="List available audio output devices")
    sub.add_parser("set-device", help="Interactively pick and save an audio device")
    sub.add_parser("auth", help="Test authentication")
    sub.add_parser("clear-cache", help="Delete cached cover art from disk")

    args = parser.parse_args()

    if args.command == "play":
        sys.exit(asyncio.run(cmd_play(args)))
    elif args.command == "devices":
        sys.exit(cmd_devices(args))
    elif args.command == "set-device":
        sys.exit(cmd_set_device(args))
    elif args.command == "auth":
        sys.exit(asyncio.run(cmd_auth(args)))
    elif args.command == "clear-cache":
        sys.exit(cmd_clear_cache(args))
    else:
        from .ui.app import QobitApp

        QobitApp().run()
        sys.exit(0)


if __name__ == "__main__":
    main()
