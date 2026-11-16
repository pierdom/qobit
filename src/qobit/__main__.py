"""qobit CLI — Phase 1 entry point.

Usage:
  qobit play <query> [--quality QUALITY]
  qobit devices
  qobit set-device
  qobit auth [status]
  qobit logout
  qobit clear-cache

Running qobit with no arguments launches the TUI.
"""

import argparse
import asyncio
import sys
import time

from . import __version__, token_store
from .account_data import erase_account_data, erase_settings, tui_running
from .audio.device import list_devices
from .audio.player import MpvPlayer
from .audio.verify import check_bit_perfect
from .config import (
    CONFIG_FILE,
    get_account,
    get_audio_device,
    get_oauth_session,
    move_token_to_keyring,
    purge_legacy_credentials,
    save_oauth_session,
    set_audio_device,
    token_location,
)
from .qobuz.client import AuthExpiredError, QobuzClient, QobuzError
from .qobuz.models import StreamUrl, Track
from .qobuz.oauth import LoginPrompt

_QUALITY_FALLBACK = ["FLAC_24_192", "FLAC_24_96", "FLAC_CD"]


_PASTE_REPLIES = {
    "no_code": "That address has no login code: copy the whole address from the "
    "browser's address bar, after logging in.",
    "wrong_login": "That address belongs to a different login: use the link above.",
}


def _show_login(prompt: LoginPrompt) -> None:
    """Print the link and, on a terminal, take a pasted redirect address:
    the browser may be on another device (SSH, a headless box), where the
    page it lands on can't load but its address carries the login code."""
    print("\nLog in to Qobuz by opening this link in a browser:\n")
    print(f"  {prompt.url}\n")
    if not sys.stdin.isatty():
        print("Waiting for the login to finish...", flush=True)
        return
    print(
        "Logging in on another device? The browser then ends on a page that "
        "doesn't load\n(localhost). Copy that page's address and paste it here.\n"
    )
    print("Waiting for the login (or a pasted address)...", flush=True)

    def on_line() -> None:
        line = sys.stdin.readline()
        if not line:  # EOF: stop reading, keep waiting for the redirect
            asyncio.get_running_loop().remove_reader(sys.stdin.fileno())
            return
        if line.strip() and (msg := _PASTE_REPLIES.get(prompt.submit(line))):
            print(msg, flush=True)

    asyncio.get_running_loop().add_reader(sys.stdin.fileno(), on_line)


async def _oauth_login(client: QobuzClient) -> bool:
    """Browser login (the link is always printed), saving the session."""
    try:
        await client.login_oauth(show_url=_show_login)
    except QobuzError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return False
    finally:
        if sys.stdin.isatty():
            asyncio.get_running_loop().remove_reader(sys.stdin.fileno())
    previous = get_account().get("name")
    current = (client.account or {}).get("name")
    if previous and current and previous != current and not tui_running():
        # Another account: the previous one's queue and mixes don't carry over.
        erase_account_data()
    save_oauth_session(*client.session(), account=client.account)
    return True


async def _authenticate(client: QobuzClient) -> bool:
    """The saved session, else a browser login."""
    app_id, token, secrets = get_oauth_session()
    if app_id and token and secrets:
        client.restore_session(app_id, token, secrets)
        return True
    print("No saved Qobuz session.")
    return await _oauth_login(client)


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
                if stream.sample:
                    print("preview only — not streamable on this account")
                    return 1
                print(f"OK ({stream.quality_badge})")
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
        bp = " ♪" if d.bit_perfect else ""
        print(f"{d.id:<55}  {d.description}{bp}{marker}")
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
    print("\nAvailable audio devices  (♪ = bit-perfect, recommended):\n")
    for i, d in enumerate(devices):
        marker = "  (current)" if d.id == current else ""
        bp = " ♪" if d.bit_perfect else "  "
        print(f"  [{i:2}]{bp}  {d.description:<45}  {d.id}{marker}")

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


def _account_line() -> str:
    account = get_account()
    name, plan = account.get("name", ""), account.get("plan", "")
    if not name:
        return "Logged in"
    return f"Logged in as {name}" + (f" ({plan})" if plan else "")


async def cmd_auth(args: argparse.Namespace) -> int:
    if args.action == "status":
        return await cmd_auth_status()
    async with QobuzClient() as client:
        if not await _oauth_login(client):
            return 1
        print(f"{_account_line()}. Session saved.")
        return 0


async def cmd_auth_status() -> int:
    app_id, token, secrets = get_oauth_session()
    where = token_location()
    if where == "keyring" and not token:
        print(
            "The session is in the system keyring, which isn't reachable here (no "
            "desktop session, or QOBIT_KEYRING=0). Run qobit from the desktop, or "
            "log in here with: qobit auth"
        )
        return 1
    if not (app_id and token):
        print("Not logged in. Run: qobit auth")
        return 1
    stored = (
        f"Token stored in {token_store.name()}."
        if where == "keyring"
        else f"Token stored in {CONFIG_FILE} (no system keyring available)."
    )
    async with QobuzClient() as client:
        client.restore_session(app_id, token, secrets)
        try:
            await client.check_session()
        except AuthExpiredError:
            print("The saved session has expired. Run: qobit auth")
            return 1
        except QobuzError as e:
            print(f"{_account_line()} (couldn't reach Qobuz to check: {e})")
            print(stored)
            return 2
    print(f"{_account_line()}. The session works.")
    print(stored)
    return 0


def _confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_logout(args: argparse.Namespace) -> int:
    if tui_running():
        print(
            "qobit is running: log out from it (^p → Log out of Qobuz), or quit it "
            "first (it would write the queue back).",
            file=sys.stderr,
        )
        return 1
    if (
        args.all
        and not args.yes
        and not _confirm(
            "This also deletes qobit's settings (audio device, theme, volume). Continue?"
        )
    ):
        print("Nothing changed.")
        return 1
    removed = erase_account_data()
    if args.all and erase_settings():
        removed.append("the settings")
    if not removed:
        print("Not logged in, and nothing of a Qobuz account on this machine.")
    else:
        print("Logged out. Erased from this machine: " + ", ".join(removed) + ".")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="qobit",
        description="Bit-perfect Qobuz client",
    )
    parser.add_argument("--version", action="version", version=f"qobit {__version__}")
    parser.add_argument(
        "--connect-on",
        action="store_true",
        help="Enable Qobuz Connect for this run (qobit appears in the Qobuz apps' "
        "device picker). Not saved — toggle it from the command palette to persist.",
    )
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
    p_auth = sub.add_parser("auth", help="Log in to Qobuz (in the browser), or check the login")
    p_auth.add_argument(
        "action",
        nargs="?",
        choices=["login", "status"],
        default="login",
        help="login (default): log in in the browser; status: check the saved session",
    )
    p_logout = sub.add_parser(
        "logout",
        help="Log out and erase the account's traces here (session, queue, history, "
        "mixes, cached covers); settings stay",
    )
    p_logout.add_argument(
        "--all", action="store_true", help="Also delete qobit's settings: nothing of qobit left"
    )
    p_logout.add_argument("--yes", "-y", action="store_true", help="Don't ask (with --all)")
    sub.add_parser("clear-cache", help="Delete cached cover art from disk")

    args = parser.parse_args()

    if purge_legacy_credentials():
        print(
            "Removed the Qobuz password an older qobit saved in config.json: "
            "qobit logs in through the browser only (qobit auth).",
            file=sys.stderr,
        )
    move_token_to_keyring()  # a token still in config.json, now that a keyring is here

    if args.command == "play":
        sys.exit(asyncio.run(cmd_play(args)))
    elif args.command == "devices":
        sys.exit(cmd_devices(args))
    elif args.command == "set-device":
        sys.exit(cmd_set_device(args))
    elif args.command == "auth":
        sys.exit(asyncio.run(cmd_auth(args)))
    elif args.command == "logout":
        sys.exit(cmd_logout(args))
    elif args.command == "clear-cache":
        sys.exit(cmd_clear_cache(args))
    else:
        from .ui.app import QobitApp

        QobitApp(connect=True if args.connect_on else None).run()
        sys.exit(0)


if __name__ == "__main__":
    main()
