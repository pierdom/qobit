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
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


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


def prompt_and_save_credentials() -> tuple[str, str]:
    print("\nqobit needs your Qobuz credentials.")
    print("Stored in ~/.config/qobit/config.json (chmod 600 recommended)\n")
    email = input("Email:    ").strip()
    password = input("Password: ").strip()
    if email and password:
        cfg = load()
        cfg["email"] = email
        cfg["password"] = password
        save(cfg)
        # Tighten permissions on the config file
        CONFIG_FILE.chmod(0o600)
        print("\nCredentials saved.\n")
    return email, password
