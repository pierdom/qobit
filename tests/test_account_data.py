"""account_data: logout erases every account trace on disk and keeps the
settings; --all leaves nothing of qobit; a running TUI blocks the CLI.
Paths are conftest's tmp redirects."""

from __future__ import annotations

import subprocess
import sys
import textwrap

from qobit import account_data, config, store
from qobit.ui import _images


def _seed():
    config.save_oauth_session("123", "tok", ["s"], {"name": "Listener", "plan": ""})
    config.set_saved_volume(80)
    for name in account_data.ACCOUNT_FILES:
        store.save(name, {"x": 1})
    covers = _images._cache_dir()
    covers.mkdir(parents=True, exist_ok=True)
    (covers / "a.jpg").write_bytes(b"1")
    (covers / "b.jpg").write_bytes(b"2")
    return covers


def test_erase_removes_every_account_trace_and_keeps_settings():
    covers = _seed()
    removed = account_data.erase_account_data()
    assert removed == [
        "the saved session",
        "the queue and its history",
        "the last track",
        "the mixes",
        "the saved copy of the library",
        "2 cached covers",
    ]
    assert config.get_oauth_session() == ("", "", [])
    assert config.get_account() == {}
    assert not any((store.DATA_DIR / n).exists() for n in account_data.ACCOUNT_FILES)
    assert list(covers.iterdir()) == []
    assert config.get_saved_volume() == 80


def test_erase_with_nothing_there_reports_nothing():
    assert account_data.erase_account_data() == []


def test_erase_settings_leaves_nothing_of_qobit():
    covers = _seed()
    account_data.hold_running_lock().close()  # the lock file exists, unheld
    account_data.erase_account_data()
    assert account_data.erase_settings() is True
    for path in (config.CONFIG_DIR, store.DATA_DIR, covers):
        assert not path.exists(), path


def test_a_running_tui_is_detected_and_released_with_it():
    assert account_data.tui_running() is False
    held = account_data.hold_running_lock()
    assert held is not None
    # flock is per open file: a second process sees it (this one would too)
    probe = textwrap.dedent(
        f"""
        import fcntl, sys
        fh = open({str(store.DATA_DIR / account_data.LOCK_FILE)!r}, "a")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit(3)
        """
    )
    assert subprocess.run([sys.executable, "-c", probe]).returncode == 3
    assert account_data.tui_running() is True
    held.close()
    assert account_data.tui_running() is False


def test_two_tuis_can_hold_the_lock_together():
    a = account_data.hold_running_lock()
    b = account_data.hold_running_lock()
    assert a is not None and b is not None
    a.close()
    assert account_data.tui_running() is True  # b still runs
    b.close()
