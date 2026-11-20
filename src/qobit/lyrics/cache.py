"""Lyrics already found, on disk: ``~/.cache/qobit/lyrics/<key>.json`` (or under
``$XDG_CACHE_HOME``), one small file per track.

- The key is the ISRC when the track has one, else the folded artist and
  title with the length: a row says what track it is for.
- Found lyrics are kept. "No lyrics" is remembered for a week (a provider may
  have them later); a failure is never remembered.
- The reader's own choices sit in the same file: the sync offset and "these
  lyrics are wrong" (remembered as none, never asked again).
- A cache, so under ``~/.cache`` with the covers; but a file per track played
  is a record of listening, so logout erases it (``account_data``) and
  ``qobit clear-cache --lyrics`` does. Files are 0600 in a 0700 directory.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .._fs import private_dir, write_private
from .matching import fold
from .model import Lyrics, Query

VERSION = 1
NONE_TTL = 7 * 24 * 3600  # seconds "no lyrics" is believed

# Where the files go; None: ~/.cache/qobit/lyrics (tests point it elsewhere).
DIR: Path | None = None

_lock = threading.Lock()
# Bumped by every erase: a save decided before it (a worker thread still
# writing the old account's lyrics) is dropped instead of bringing a file back.
_epoch = 0


def _dir() -> Path:
    if DIR is not None:
        return DIR
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "qobit" / "lyrics"


def key(query: Query) -> str:
    ident = f"isrc:{query.isrc.upper()}" if query.isrc else None
    ident = ident or f"{fold(query.artist)}|{fold(query.title)}|{query.duration}"
    return hashlib.sha1(ident.encode()).hexdigest()[:24]


def _path(query: Query) -> Path:
    return _dir() / f"{key(query)}.json"


@dataclass
class Entry:
    lyrics: Lyrics | None  # None: none were found
    fetched_at: float
    offset: float = 0.0  # seconds the reader shifted the timing by
    rejected: bool = False  # the reader said these were wrong

    def believed(self, now: float | None = None) -> bool:
        """Whether this answer stands without asking a provider again."""
        if self.lyrics is not None or self.rejected:
            return True
        return (now if now is not None else time.time()) - self.fetched_at < NONE_TTL


def load(query: Query) -> Entry | None:
    try:
        data = json.loads(_path(query).read_text())
        if data["version"] != VERSION:
            return None
        raw = data.get("lyrics")
        return Entry(
            lyrics=Lyrics.from_json(raw) if raw else None,
            fetched_at=float(data["fetched_at"]),
            offset=float(data.get("offset") or 0.0),
            rejected=bool(data.get("rejected")),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def epoch() -> int:
    """The erase count: take it where a save is decided, pass it to ``save``."""
    return _epoch


def _write(query: Query, entry: Entry) -> None:
    private_dir(_dir().parent)
    private_dir(_dir())
    text = json.dumps(
        {
            "version": VERSION,
            "fetched_at": entry.fetched_at,
            "offset": entry.offset,
            "rejected": entry.rejected,
            "lyrics": entry.lyrics.to_json() if entry.lyrics else None,
        }
    )
    write_private(_path(query), text)


def save(query: Query, lyrics: Lyrics | None, since: int | None = None) -> None:
    """Remember what was found (or that there is nothing), keeping the
    reader's offset. Nothing if the files were erased after ``since``."""
    with _lock:
        if since is not None and since != _epoch:
            return
        old = load(query)
        entry = Entry(lyrics=lyrics, fetched_at=time.time())
        if old is not None:
            entry.offset, entry.rejected = old.offset, old.rejected
        _write(query, entry)


def update(query: Query, *, offset: float | None = None, reject: bool = False) -> None:
    """The reader's choices: a new offset, or these lyrics are wrong (they
    stop showing; the answer is kept so it is not asked for again)."""
    with _lock:
        entry = load(query) or Entry(lyrics=None, fetched_at=time.time())
        if offset is not None:
            entry.offset = offset
        if reject:
            entry.rejected, entry.lyrics = True, None
        _write(query, entry)


def clear() -> int:
    """Delete the files and their directory (logout, ``qobit clear-cache``).
    Returns the bytes freed."""
    global _epoch
    with _lock:
        _epoch += 1
        freed = 0
        directory = _dir()
        try:
            files = list(directory.glob("*.json"))
        except OSError:
            files = []
        for path in files:
            with contextlib.suppress(OSError):
                size = path.stat().st_size
                path.unlink()
                freed += size
        with contextlib.suppress(OSError):
            directory.rmdir()  # only if empty: never anything we didn't write
    return freed
