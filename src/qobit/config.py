import json
import os
from pathlib import Path
from typing import Any

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
    """Write atomically (tmp file + rename) so a crash/kill mid-write can't
    leave a truncated config behind, and chmod 0600 on every write — this file
    may hold a plaintext password or OAuth token, so every writer gets the
    same protection rather than relying on call sites to remember it."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, CONFIG_FILE)
    try:
        CONFIG_FILE.chmod(0o600)
    except Exception:
        pass


def get_credentials() -> tuple[str, str]:
    email = os.environ.get("QOBUZ_EMAIL", "")
    password = os.environ.get("QOBUZ_PASSWORD", "")
    if email and password:
        return email, password
    cfg = load()
    return cfg.get("email", ""), cfg.get("password", "")


def get_audio_device() -> str | None:
    return os.environ.get("QOBUZ_AUDIO_DEVICE") or load().get("audio_device") or None


def set_audio_device(device_id: str) -> None:
    cfg = load()
    cfg["audio_device"] = device_id
    save(cfg)


def get_oauth_session() -> tuple[str, str, list[str]]:
    """Returns (app_id, token, secrets) or empty values if no saved session."""
    s = load().get("oauth_session", {})
    secrets = s.get("secrets") or ([s["secret"]] if s.get("secret") else [])
    return s.get("app_id", ""), s.get("token", ""), secrets


def save_oauth_session(app_id: str, token: str, secrets: list[str]) -> None:
    cfg = load()
    cfg["oauth_session"] = {"app_id": app_id, "token": token, "secrets": secrets}
    save(cfg)


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


def get_radio_mode() -> bool:
    return bool(load().get("radio_mode", False))


def set_radio_mode(value: bool) -> None:
    cfg = load()
    cfg["radio_mode"] = value
    save(cfg)


def prompt_and_save_credentials() -> tuple[str, str]:
    print("\nqobit needs your Qobuz credentials.")
    print("Stored in ~/.config/qobit/config.json (chmod 600 applied automatically)\n")
    email = input("Email:    ").strip()
    password = input("Password: ").strip()
    if email and password:
        cfg = load()
        cfg["email"] = email
        cfg["password"] = password
        save(cfg)
        print("\nCredentials saved.\n")
    return email, password
