import contextlib
import json
import os
from pathlib import Path
from typing import Any

from . import token_store
from ._fs import private_dir, write_private
from .secret import secret

CONFIG_DIR = Path.home() / ".config" / "qobit"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


def save(data: dict[str, Any]) -> None:
    """Write atomically, 0600 from the first byte (``_fs.write_private``): this
    file holds the Qobuz session token. Legacy plaintext credentials, from
    the removed email/password login, are dropped on every write."""
    for key in LEGACY_CREDENTIAL_KEYS:
        data.pop(key, None)
    private_dir(CONFIG_DIR)
    write_private(CONFIG_FILE, json.dumps(data, indent=2))


# Saved by the email/password fallback of qobit <= 0.7, which stored the
# password in plaintext and couldn't stream anyway. Never read; scrubbed.
LEGACY_CREDENTIAL_KEYS = ("email", "password")


def purge_legacy_credentials() -> bool:
    """Startup cleanup after older qobits. Removes a saved plaintext password
    (True: worth telling the user) and the `config.json.tmp` they could leave
    behind, written world-readable before its chmod. Also rewrites a
    config.json or directory that others can read (0600 / 0700)."""
    with contextlib.suppress(OSError):
        CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.tmp").unlink()
    if not CONFIG_FILE.exists():
        return False
    cfg = load()
    had_password = any(k in cfg for k in LEGACY_CREDENTIAL_KEYS)
    loose = (CONFIG_FILE.stat().st_mode | CONFIG_DIR.stat().st_mode) & 0o077
    if had_password or loose:
        save(cfg)
    return had_password


def get_audio_device() -> str | None:
    return os.environ.get("QOBUZ_AUDIO_DEVICE") or load().get("audio_device") or None


def set_audio_device(device_id: str) -> None:
    cfg = load()
    cfg["audio_device"] = device_id
    save(cfg)


def get_oauth_session() -> tuple[str, str, list[str]]:
    """Returns (app_id, token, secrets) or empty values if no saved session.
    The token comes from the system keyring when the session says it's
    there (token_store.py), else from this file."""
    s = load().get("oauth_session") or {}
    secrets = s.get("secrets") or ([s["secret"]] if s.get("secret") else [])
    in_keyring = s.get("token_store") == "keyring"
    token = (token_store.load() or "") if in_keyring else s.get("token", "")
    return s.get("app_id", ""), secret(token) or "", secrets


def token_location() -> str:
    """Where the saved token is: "keyring", "file" or "" (no session)."""
    s = load().get("oauth_session") or {}
    if s.get("token_store") == "keyring":
        return "keyring"
    return "file" if s.get("token") else ""


def save_oauth_session(
    app_id: str, token: str, secrets: list[str], account: dict[str, str] | None = None
) -> None:
    """Save a session: the token in the system keyring when there is one
    (only a marker here), else in this file (0600)."""
    cfg = load()
    session: dict = {"app_id": app_id, "secrets": secrets}
    if token_store.store(token):
        session["token_store"] = "keyring"
    else:
        session["token"] = token
    if account:
        session["account"] = account
    cfg["oauth_session"] = session
    save(cfg)


def move_token_to_keyring() -> bool:
    """Startup: a token saved in this file (older qobit, or no keyring back
    then) moves to the keyring when one is usable now. True if moved."""
    cfg = load()
    s = cfg.get("oauth_session") or {}
    token = s.get("token")
    if not token or s.get("token_store") == "keyring" or not token_store.store(token):
        return False
    del s["token"]
    s["token_store"] = "keyring"
    cfg["oauth_session"] = s
    save(cfg)
    return True


def get_account() -> dict[str, str]:
    """``{"name", "plan"}`` saved at login (empty for older sessions)."""
    return (load().get("oauth_session") or {}).get("account") or {}


def clear_oauth_session() -> bool:
    """Forget the saved session (log out), in the keyring too. True if
    there was one."""
    cfg = load()
    if "oauth_session" not in cfg:
        return False
    if (cfg["oauth_session"] or {}).get("token_store") == "keyring":
        token_store.delete()
    del cfg["oauth_session"]
    save(cfg)
    return True


def get_saved_theme() -> str | None:
    return load().get("theme") or None


def set_saved_theme(value: str) -> None:
    cfg = load()
    cfg["theme"] = value
    save(cfg)


def get_transparent_background() -> bool:
    return bool(load().get("transparent_background", False))


def set_transparent_background(value: bool) -> None:
    cfg = load()
    cfg["transparent_background"] = value
    save(cfg)


def get_image_mode() -> str:
    """How cover art is drawn: "auto" (default), "kitty", "blocks" or "off"
    (see ui._images.IMAGE_MODES)."""
    return str(load().get("images", "auto"))


def set_image_mode(value: str) -> None:
    cfg = load()
    cfg["images"] = value
    save(cfg)


def get_animations() -> bool:
    """The wordmark's colour cycle and the playing row's bar meter (on by
    default; off saves CPU on slow machines)."""
    return bool(load().get("animations", True))


def set_animations(value: bool) -> None:
    cfg = load()
    cfg["animations"] = value
    save(cfg)


def get_gapless() -> bool:
    """Gapless playback between tracks (on by default)."""
    return bool(load().get("gapless", True))


def set_gapless(value: bool) -> None:
    cfg = load()
    cfg["gapless"] = value
    save(cfg)


def get_radio_mode() -> bool:
    return bool(load().get("radio_mode", False))


def set_radio_mode(value: bool) -> None:
    cfg = load()
    cfg["radio_mode"] = value
    save(cfg)


def get_connect_enabled() -> bool:
    """Qobuz Connect renderer (qobit shows up in the Qobuz apps' device
    picker). Off by default: being visible on the account whenever qobit runs
    is a surprise unless asked for. ``qobit --connect-on`` enables it for one
    run without saving."""
    return bool(load().get("connect_enabled", False))


def set_connect_enabled(value: bool) -> None:
    cfg = load()
    cfg["connect_enabled"] = value
    save(cfg)


def get_software_volume_enabled() -> bool:
    """Off by default: qobit's whole purpose is the DAC receiving exactly the
    bits from the file, and software volume is DSP — it scales every sample
    before playback. Must be explicitly opted into."""
    return bool(load().get("software_volume_enabled", False))


def set_software_volume_enabled(value: bool) -> None:
    cfg = load()
    cfg["software_volume_enabled"] = value
    save(cfg)


def get_saved_volume() -> int:
    """0-100. Defaults to 100 (unity gain) so merely enabling software volume
    doesn't itself alter the signal until the user actually lowers it."""
    return int(load().get("volume", 100))


def set_saved_volume(value: int) -> None:
    cfg = load()
    cfg["volume"] = value
    save(cfg)
