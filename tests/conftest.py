"""Shared fixtures.

The autouse fixture below is load-bearing: config.py and store.py read/write
real paths under the user's home directory (~/.config/qobit, ~/.local/share/
qobit). Without redirecting those paths, running this suite would read — and
in some tests, overwrite — a developer's actual saved Qobuz session, device
config, and player state. Every test gets the redirect automatically so no
individual test has to remember to ask for it.
"""

from __future__ import annotations

import pytest

import qobit.ui.app as app_module
from qobit import config, store
from qobit.ui import _images
from qobit.ui.screens import queue as queue_module


class FakeKeyring:
    """In-memory stand-in for the ``keyring`` module: tests must never read
    or write the developer's real Keychain / Secret Service. ``fail = True``
    makes it behave like a machine without a usable keyring."""

    name = "Fake keyring"

    def __init__(self):
        self.items: dict[tuple[str, str], str] = {}
        self.fail = False

    def get_keyring(self):
        return self

    def _check(self):
        if self.fail:
            raise RuntimeError("no keyring")

    def set_password(self, service, account, password):
        self._check()
        self.items[(service, account)] = password

    def get_password(self, service, account):
        self._check()
        return self.items.get((service, account))

    def delete_password(self, service, account):
        self._check()
        del self.items[(service, account)]


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    from qobit import token_store

    kr = FakeKeyring()
    monkeypatch.setattr(token_store, "_keyring", lambda: kr)
    monkeypatch.setattr(token_store, "enabled", lambda: True)
    return kr


@pytest.fixture(autouse=True)
def isolated_filesystem_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config" / "config.json")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(_images, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(_images, "_image_mode", None)  # a test that set it leaks to the next
    # A developer's real shell may export these for CLI convenience (the
    # README documents QOBUZ_AUDIO_DEVICE as a valid override); without
    # clearing them, "nothing configured" tests would pass or fail depending
    # on whoever's machine runs the suite.
    for var in ("QOBUZ_AUDIO_DEVICE",):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture(autouse=True)
def isolated_image_memory_cache():
    """_IMAGE_CACHE is module-level global state shared across the whole test
    session; clear it before and after every test so tests can't see each
    other's cached images or silently depend on ordering."""
    _images._IMAGE_CACHE.clear()
    yield
    _images._IMAGE_CACHE.clear()


# ── headless UI ──────────────────────────────────────────────────────────────


class _NoOpMediaKeys:
    def __init__(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass

    def set_artwork(self, *args, **kwargs):
        pass

    def close(self):
        pass


class StubClient:
    # Logged in, so QobitApp starts without its login dialog.
    user_auth_token = "stub-token"
    account = None

    def __init__(self, *args, **kwargs):
        self.on_auth_expired = None

    def restore_session(self, *args, **kwargs):
        return True

    def clear_caches(self):
        pass

    def is_closed(self):
        return False

    async def close(self):
        pass

    async def aclose(self):
        pass

    async def get_all_favorite_tracks(self):
        return []

    async def get_all_favorite_albums(self):
        return []

    async def get_all_favorite_artists(self):
        return []


async def _no_image(url):
    return None


@pytest.fixture
def ui_app(monkeypatch):
    """The real QobitApp for headless UI tests (``app.run_test()``): a stub
    client (no network), no media keys, no images, no Connect, theme not
    saved. Never starts mpv: tests set the timeline directly."""
    monkeypatch.setattr(app_module, "MediaKeys", _NoOpMediaKeys)
    monkeypatch.setattr(app_module, "QobuzClient", StubClient)
    monkeypatch.setattr(app_module, "set_saved_theme", lambda *_: None)
    monkeypatch.setattr(queue_module, "fetch_image", _no_image)
    from qobit.ui.widgets import transport

    monkeypatch.setattr(transport, "fetch_image", _no_image)
    return app_module.QobitApp(connect=False)
