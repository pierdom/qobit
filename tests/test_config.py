"""qobit.config — credentials + device/theme/session config. conftest.py's
autouse fixture redirects CONFIG_DIR/CONFIG_FILE to a tmp dir, so nothing
here reads or overwrites a real ~/.config/qobit/config.json."""

from __future__ import annotations

import stat

from qobit import config


def test_load_missing_config_returns_empty_dict():
    assert config.load() == {}


def test_save_then_load_round_trips():
    config.save({"theme": "nord"})
    assert config.load() == {"theme": "nord"}


def test_save_sets_restrictive_permissions():
    """config.json can hold a plaintext password or OAuth token; every write
    must leave it chmod 600 regardless of the caller (see config.save's
    docstring) — this is the one thing worth pinning down with a real stat()
    rather than trusting the chmod call was reached."""
    config.save({"email": "a@b.com"})
    mode = stat.S_IMODE(config.CONFIG_FILE.stat().st_mode)
    assert mode == 0o600


def test_get_credentials_from_config_file():
    config.save({"email": "user@example.com", "password": "secret"})
    assert config.get_credentials() == ("user@example.com", "secret")


def test_get_credentials_env_vars_take_priority_over_config_file(monkeypatch):
    config.save({"email": "file@example.com", "password": "filepass"})
    monkeypatch.setenv("QOBUZ_EMAIL", "env@example.com")
    monkeypatch.setenv("QOBUZ_PASSWORD", "envpass")
    assert config.get_credentials() == ("env@example.com", "envpass")


def test_get_credentials_missing_returns_empty_strings():
    assert config.get_credentials() == ("", "")


def test_audio_device_round_trip():
    assert config.get_audio_device() is None
    config.set_audio_device("alsa/hw:CARD=DAC,DEV=0")
    assert config.get_audio_device() == "alsa/hw:CARD=DAC,DEV=0"


def test_audio_device_env_var_overrides_saved_config(monkeypatch):
    config.set_audio_device("alsa/hw:CARD=DAC,DEV=0")
    monkeypatch.setenv("QOBUZ_AUDIO_DEVICE", "coreaudio/override")
    assert config.get_audio_device() == "coreaudio/override"


def test_oauth_session_round_trip():
    config.save_oauth_session("app123", "token456", ["secretA", "secretB"])
    app_id, token, secrets = config.get_oauth_session()
    assert app_id == "app123"
    assert token == "token456"
    assert secrets == ["secretA", "secretB"]


def test_oauth_session_missing_returns_empty_values():
    assert config.get_oauth_session() == ("", "", [])


def test_oauth_session_accepts_legacy_single_secret_key():
    """Older config.json files may predate the multi-secret `secrets` list
    and only have a single `secret` string — get_oauth_session must still
    read those without the caller crashing on a missing key."""
    config.save({"oauth_session": {"app_id": "a", "token": "t", "secret": "only-one"}})
    app_id, token, secrets = config.get_oauth_session()
    assert secrets == ["only-one"]


def test_theme_round_trip():
    assert config.get_saved_theme() is None
    config.set_saved_theme("gruvbox")
    assert config.get_saved_theme() == "gruvbox"


def test_transparent_background_round_trip():
    assert config.get_transparent_background() is False
    config.set_transparent_background(True)
    assert config.get_transparent_background() is True


def test_radio_mode_round_trip():
    assert config.get_radio_mode() is False
    config.set_radio_mode(True)
    assert config.get_radio_mode() is True


def test_save_preserves_unrelated_existing_keys():
    config.set_saved_theme("nord")
    config.set_radio_mode(True)
    cfg = config.load()
    assert cfg["theme"] == "nord"
    assert cfg["radio_mode"] is True
