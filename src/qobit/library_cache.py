"""The library on disk: favourite tracks, albums and artists, and the user's
playlists, as they were last fetched.

Fetching them takes seconds at every start (each list comes in pages, and a
slow network or a Raspberry Pi makes it worse), and the Tracks, Albums,
Artists and Playlists tabs had nothing to show until then. They now show
this copy at once; the app fetches the lists again in the background and
updates the tabs only if something changed.

One file per list (``~/.cache/qobit/library/<kind>.json``, or under
``$XDG_CACHE_HOME``), so a toggled heart rewrites one list, not the library,
and nothing stays in memory here. A cache: Qobuz has it all, so it sits with
the covers, where backups and cleaners skip it. A file written for another
account, or by another version of this format, counts as empty. It holds
what the account listens to: logout erases it
(``account_data.erase_account_data`` calls ``clear``). UI-agnostic.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import threading
from pathlib import Path
from typing import Any

from ._fs import private_dir, write_private
from .qobuz.models import Album, Artist, Playlist, Track
from .qobuz.sanitize import clean_json

# Bump when a model's fields change in a way old entries can't be read back:
# an old file then counts as empty and is rewritten after the next fetch.
VERSION = 1
KINDS = ("tracks", "albums", "artists", "playlists")


# Where the files go; None: ~/.cache/qobit/library (tests point it elsewhere).
DIR: Path | None = None


def _dir() -> Path:
    if DIR is not None:
        return DIR
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "qobit" / "library"


def path(kind: str) -> Path:
    """The file of ``kind`` (one of KINDS)."""
    return _dir() / f"{kind}.json"


_lock = threading.Lock()  # saves run in worker threads
# Bumped before each erase (logout, clear-cache): a save decided before it (a
# worker thread still writing the old account's lists) is dropped instead of
# bringing a file back.
_epoch = 0
# Per kind, the last save written: saves run in a thread pool and can finish
# out of order; an older list never overwrites a newer one.
_ticket = 0
_written: dict[str, int] = {}


def _track(d: dict) -> Track:
    return Track(**d)


def _with_tracks(cls: type, d: dict) -> Any:
    d = dict(d)
    d["tracks"] = [_track(t) for t in d.get("tracks") or ()]
    if cls is Artist:
        d["albums"] = [_album(a) for a in d.get("albums") or ()]
    return cls(**d)


def _album(d: dict) -> Album:
    return _with_tracks(Album, d)


_BUILD = {
    "tracks": _track,
    "albums": _album,
    "artists": lambda d: _with_tracks(Artist, d),
    "playlists": lambda d: _with_tracks(Playlist, d),
}


def load(kind: str, account: str) -> list[Any] | None:
    """The saved list of ``kind`` (one of KINDS) for ``account``; None if
    there's none, or it can't be read back."""
    try:
        # Cleaned like every state file: it's on disk, anyone could edit it.
        data = clean_json(json.loads(path(kind).read_text()))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != VERSION or data.get("account") != account:
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None
    try:
        return [_BUILD[kind](d) for d in items]
    except (TypeError, ValueError, AttributeError):  # fields changed: refetch
        return None


def epoch() -> int:
    """The current erase count: take it where a save is decided, pass it to
    ``save``."""
    return _epoch


def ticket() -> int:
    """A number for a save, taken where it's decided (so in order)."""
    global _ticket
    with _lock:
        _ticket += 1
        return _ticket


def save(
    kind: str, items: list[Any], account: str, since: int | None = None, number: int | None = None
) -> None:
    """Save ``items`` (models) as the list of ``kind`` for ``account``.
    Nothing if the files were erased after ``since`` (an ``epoch()``), or a
    save with a later ``number`` (a ``ticket()``) was written already."""
    rows = [dataclasses.asdict(i) for i in items]
    with _lock:
        if since is not None and since != _epoch:
            return
        if number is not None:
            if number < _written.get(kind, 0):
                return
            _written[kind] = number
        directory = _dir()
        private_dir(directory.parent)  # 0700 both: it's a record of listening
        private_dir(directory)
        text = json.dumps({"version": VERSION, "account": account, "items": rows})
        write_private(path(kind), text)


def cancel_saves() -> None:
    """Drop every save not written yet: call before erasing the files."""
    global _epoch
    with _lock:
        _epoch += 1


def clear() -> int:
    """Delete the files and their directory (``qobit clear-cache``, logout).
    Returns the bytes freed."""
    cancel_saves()
    freed = 0
    for kind in KINDS:
        try:
            size = path(kind).stat().st_size
            path(kind).unlink()
            freed += size
        except FileNotFoundError:
            pass
    with contextlib.suppress(OSError):
        _dir().rmdir()  # only if empty: never anything we didn't write
    return freed
