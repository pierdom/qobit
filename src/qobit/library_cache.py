"""The library on disk: favourite tracks, albums and artists, and the user's
playlists, as they were last fetched.

Fetching them takes seconds at every start (each list comes in pages, and a
slow network or a Raspberry Pi makes it worse), and the Tracks, Albums,
Artists and Playlists tabs had nothing to show until then. They now show
this copy at once; the app fetches the lists again in the background and
updates the tabs only if something changed.

One file, ``DATA_DIR/library_cache.json``, for one account: written for
another account (or by another version of this format) it counts as empty.
It holds what the account listens to, so it's in
``account_data.ACCOUNT_FILES`` (logout erases it). UI-agnostic.
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Any

from . import store
from .qobuz.models import Album, Artist, Playlist, Track

FILE = "library_cache.json"
# Bump when a model's fields change in a way old entries can't be read back:
# an old file then counts as empty and is rewritten after the next fetch.
VERSION = 1
KINDS = ("tracks", "albums", "artists", "playlists")

_lock = threading.Lock()  # saves run in worker threads
_data: dict[str, Any] | None = None  # the file, read once per process


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


def _file(account: str) -> dict[str, Any]:
    """The file's contents if they belong to ``account``, else a new one."""
    global _data
    if _data is None:
        _data = store.load(FILE)
    if _data.get("version") != VERSION or _data.get("account") != account:
        _data = {"version": VERSION, "account": account}
    return _data


def load(kind: str, account: str) -> list[Any] | None:
    """The saved list of ``kind`` (one of KINDS) for ``account``; None if
    there's none, or it can't be read back."""
    with _lock:
        items = _file(account).get(kind)
    if not isinstance(items, list):
        return None
    try:
        return [_BUILD[kind](d) for d in items]
    except (TypeError, ValueError, AttributeError):  # fields changed: refetch
        return None


def save(kind: str, items: list[Any], account: str) -> None:
    """Save ``items`` (models) as the list of ``kind`` for ``account``."""
    rows = [dataclasses.asdict(i) for i in items]
    with _lock:
        data = _file(account)
        data[kind] = rows
        store.save(FILE, data)


def forget() -> None:
    """Drop the copy read into memory (after the file was erased)."""
    global _data
    with _lock:
        _data = None
