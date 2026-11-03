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

from qobit import config, store
from qobit.ui import _images


@pytest.fixture(autouse=True)
def isolated_filesystem_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config" / "config.json")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(_images, "_CACHE_DIR", tmp_path / "cache")
    # A developer's real shell may export these for CLI convenience (the
    # README documents QOBUZ_AUDIO_DEVICE as a valid override); without
    # clearing them, "nothing configured" tests would pass or fail depending
    # on whoever's machine runs the suite.
    for var in ("QOBUZ_EMAIL", "QOBUZ_PASSWORD", "QOBUZ_AUDIO_DEVICE"):
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
