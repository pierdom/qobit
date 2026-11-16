"""qobit.config — credentials + device/theme/session config. conftest.py's
autouse fixture redirects CONFIG_DIR/CONFIG_FILE to a tmp dir, so nothing
here reads or overwrites a real ~/.config/qobit/config.json."""

from __future__ import annotations

import stat
from pathlib import Path

from qobit import config


def test_load_missing_config_returns_empty_dict():
    assert config.load() == {}


def test_save_then_load_round_trips():
    config.save({"theme": "nord"})
    assert config.load() == {"theme": "nord"}


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_save_sets_restrictive_permissions():
    """config.json holds the session token: 0600, in a 0700 directory, on
    every write, whatever the caller. Pinned with a real stat()."""
    config.save({"theme": "nord"})
    assert _mode(config.CONFIG_FILE) == 0o600
    assert _mode(config.CONFIG_DIR) == 0o700


def test_save_is_private_from_the_first_byte(monkeypatch):
    """The old save wrote a umask-mode temp file and chmod-ed after the
    rename: a window (or, if chmod failed, forever) where others could read
    the token. Every file qobit creates must already be 0600."""
    import os

    created = []
    real_replace = os.replace

    def spy_replace(src, dst):
        created.append(_mode(Path(src)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    os.umask(0o022)
    config.save({"oauth_session": {"token": "t"}})
    assert created == [0o600]
    assert [p.name for p in config.CONFIG_DIR.iterdir()] == ["config.json"]


def test_save_tightens_a_directory_left_world_readable():
    config.CONFIG_DIR.mkdir(parents=True, mode=0o755)
    config.CONFIG_DIR.chmod(0o755)
    config.save({"theme": "nord"})
    assert _mode(config.CONFIG_DIR) == 0o700


def test_save_drops_legacy_plaintext_credentials():
    config.save({"email": "a@b.com", "password": "pw", "theme": "nord"})
    assert config.load() == {"theme": "nord"}


def _write_legacy(data, mode=0o644):
    import json

    config.CONFIG_DIR.mkdir(parents=True)
    config.CONFIG_FILE.write_text(json.dumps(data))
    config.CONFIG_FILE.chmod(mode)


def test_purge_removes_a_saved_password_and_keeps_the_rest():
    _write_legacy({"email": "a@b.com", "password": "pw", "oauth_session": {"token": "t"}})
    assert config.purge_legacy_credentials() is True
    assert config.load() == {"oauth_session": {"token": "t"}}
    assert _mode(config.CONFIG_FILE) == 0o600


def test_purge_tightens_a_readable_config_without_reporting_a_password():
    _write_legacy({"oauth_session": {"token": "t"}})
    assert config.purge_legacy_credentials() is False
    assert _mode(config.CONFIG_FILE) == 0o600
    assert config.load() == {"oauth_session": {"token": "t"}}


def test_purge_deletes_a_leftover_temp_copy():
    _write_legacy({"theme": "nord"}, mode=0o600)
    stale = config.CONFIG_FILE.with_name("config.json.tmp")
    stale.write_text('{"oauth_session": {"token": "t"}}')
    config.purge_legacy_credentials()
    assert not stale.exists()


def test_purge_without_a_config_is_a_no_op():
    assert config.purge_legacy_credentials() is False
    assert not config.CONFIG_FILE.exists()


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
    _, _, secrets = config.get_oauth_session()
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
