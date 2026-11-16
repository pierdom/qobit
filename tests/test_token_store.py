"""The session token in the system keyring (token_store.py, config.py). The
autouse ``fake_keyring`` (conftest) stands in for the real one; ``fail``
simulates a machine without a usable keyring."""

from __future__ import annotations

import json
import os

import pytest

from qobit import account_data, config, token_store
from qobit.token_store import _quiet_stderr
from qobit.token_store import enabled as real_enabled  # before conftest patches it


def _raw_session() -> dict:
    return json.loads(config.CONFIG_FILE.read_text())["oauth_session"]


def test_the_token_goes_to_the_keyring_not_the_file(fake_keyring):
    config.save_oauth_session("123", "secret-token", ["s"], {"name": "L", "plan": ""})
    raw = _raw_session()
    assert "token" not in raw and raw["token_store"] == "keyring"
    assert "secret-token" not in config.CONFIG_FILE.read_text()
    assert fake_keyring.items[(token_store.SERVICE, token_store.ACCOUNT)] == "secret-token"
    assert config.get_oauth_session() == ("123", "secret-token", ["s"])
    assert config.token_location() == "keyring"


def test_without_a_keyring_the_token_stays_in_the_file(fake_keyring):
    fake_keyring.fail = True
    config.save_oauth_session("123", "secret-token", ["s"])
    assert _raw_session()["token"] == "secret-token"
    assert "token_store" not in _raw_session()
    assert config.get_oauth_session() == ("123", "secret-token", ["s"])
    assert config.token_location() == "file"


def test_a_keyring_that_cannot_read_back_counts_as_none(fake_keyring, monkeypatch):
    monkeypatch.setattr(fake_keyring, "get_password", lambda *a: None)
    assert token_store.store("t") is False
    config.save_oauth_session("123", "t", ["s"])
    assert _raw_session()["token"] == "t"


def test_a_file_token_moves_to_the_keyring_at_startup(fake_keyring):
    fake_keyring.fail = True
    config.save_oauth_session("123", "old-token", ["s"])
    fake_keyring.fail = False  # e.g. now in a desktop session
    assert config.move_token_to_keyring() is True
    assert "token" not in _raw_session()
    assert config.get_oauth_session()[1] == "old-token"
    assert config.move_token_to_keyring() is False  # nothing left to move


def test_no_move_without_a_keyring(fake_keyring):
    fake_keyring.fail = True
    config.save_oauth_session("123", "old-token", ["s"])
    assert config.move_token_to_keyring() is False
    assert _raw_session()["token"] == "old-token"


def test_an_unreachable_keyring_reads_as_logged_out_but_says_where(fake_keyring):
    config.save_oauth_session("123", "t", ["s"])
    fake_keyring.fail = True  # e.g. over SSH, no desktop session
    assert config.get_oauth_session()[1] == ""
    assert config.token_location() == "keyring"


def test_logout_removes_the_keyring_entry(fake_keyring):
    config.save_oauth_session("123", "t", ["s"])
    account_data.erase_account_data()
    assert fake_keyring.items == {}
    assert config.token_location() == ""


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, True),
        ("darwin", {"QOBIT_KEYRING": "0"}, False),
        ("linux", {}, False),  # no D-Bus session: no Secret Service to ask
        ("linux", {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}, True),
        ("linux", {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/x", "QOBIT_KEYRING": "off"}, False),
    ],
)
def test_enabled(monkeypatch, platform, env, expected):
    monkeypatch.setattr(token_store.sys, "platform", platform)
    for var in ("QOBIT_KEYRING", "DBUS_SESSION_BUS_ADDRESS"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert real_enabled() is expected


def test_keyring_calls_cannot_write_to_the_terminal(capfd):
    """GLib warns straight to fd 2 while Linux backends are probed; inside
    the TUI that garbles the screen."""
    with _quiet_stderr():
        os.write(2, b"g_object_unref: assertion failed\n")
    os.write(2, b"after\n")
    assert capfd.readouterr().err == "after\n"
