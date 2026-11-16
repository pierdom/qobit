"""What a Qobuz account leaves on this machine, and erasing it.

Logging out erases it all (a shared machine must not show the next person
the last one's listening): the session, the queue and its history, the last
track, the mixes, the cover-art cache. Settings stay (audio device, theme,
volume, the radio and Connect switches): they belong to the machine, not the
account. ``erase_settings`` removes those too (``qobit logout --all``).

A running TUI rewrites the queue within a second, so the CLI erases nothing
while one runs: every TUI holds a shared flock on ``DATA_DIR/qobit.lock`` for
its lifetime (``hold_running_lock``), and ``tui_running`` tries an exclusive
one. The OS drops the lock with the process, so a crash leaves none behind.
UI-agnostic: the TUI's own logout resets its memory, then calls in here.
"""

import contextlib
import fcntl
from typing import IO

from . import config, store

# Every file in DATA_DIR that carries account activity.
ACCOUNT_FILES = ("queue_state.json", "player_state.json", "mixes_cache.json")
# The stream playlist (audio/player.py) holds a signed URL naming the
# account: normally removed when playback stops, erased here if left over.
_LEFTOVER_FILES = ("stream.m3u",)
# Empty marker/lock files and the mpv socket: no account data, removed only
# with the settings (nothing of qobit left).
_RUNTIME_FILES = ("qobit.lock", "connect.lock", "mpv.sock")
LOCK_FILE = "qobit.lock"


def hold_running_lock() -> IO[str] | None:
    """Mark this process as a running TUI (shared: several may run). Keep the
    returned file open for the process's lifetime."""
    try:
        fh = open(store._path(LOCK_FILE), "a")  # noqa: SIM115 — held open: the lock
        fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return None
    return fh


def tui_running() -> bool:
    """Whether a qobit TUI is running on this machine (as this user)."""
    path = store.DATA_DIR / LOCK_FILE
    if not path.exists():
        return False
    try:
        with open(path, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:
        return True
    return False


def erase_account_data() -> list[str]:
    """Forget the session and delete every account trace on disk. Returns
    what was removed, as phrases for a summary line."""
    from .ui._images import clear_disk_cache

    removed: list[str] = []
    if config.clear_oauth_session():
        removed.append("the saved session")
    names = {
        "queue_state.json": "the queue and its history",
        "player_state.json": "the last track",
        "mixes_cache.json": "the mixes",
    }
    for name in ACCOUNT_FILES:
        path = store.DATA_DIR / name
        if path.exists():
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
                removed.append(names[name])
    for name in _LEFTOVER_FILES:
        with contextlib.suppress(FileNotFoundError):
            (store.DATA_DIR / name).unlink()
    _, covers, _ = clear_disk_cache()
    if covers:
        removed.append(f"{covers} cached cover{'s' if covers != 1 else ''}")
    return removed


def erase_settings() -> bool:
    """Delete config.json and qobit's runtime files, then its directories if
    empty: nothing of qobit left. Call after ``erase_account_data``."""
    from .ui._images import _cache_dir

    existed = config.CONFIG_FILE.exists()
    with contextlib.suppress(FileNotFoundError):
        config.CONFIG_FILE.unlink()
    for name in _RUNTIME_FILES:
        with contextlib.suppress(FileNotFoundError):
            (store.DATA_DIR / name).unlink()
    cache = _cache_dir()
    for directory in (config.CONFIG_DIR, store.DATA_DIR, cache, cache and cache.parent):
        if directory is not None:
            with contextlib.suppress(OSError):
                directory.rmdir()  # only if empty: never anything we didn't write
    return existed
