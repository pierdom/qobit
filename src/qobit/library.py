"""The user's library in memory: favourite tracks and albums, followed artists
and playlists, each loaded once and kept in step with the server.

Each list shows from the disk copy at once (``library_cache``) and is fetched
again in the background; a fetch only replaces it if it differs. A toggle
changes the server, then the list, then the disk copy. What shows the lists
(the tabs, the hearts) is told through ``on_change``: this module knows no
Textual.

``None`` for a list means "not loaded yet", which is not the same as empty.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from collections.abc import Callable
from functools import partial
from typing import Any, Literal, NamedTuple

from . import library_cache
from .qobuz.client import QobuzClient, QobuzError
from .qobuz.models import Album, Artist, Playlist, Track


class LibraryError(Exception):
    """A toggle that didn't happen; the message says why, for the user."""


class Change(NamedTuple):
    """What changed, for whatever shows it. ``item_id`` and ``state`` (now a
    favourite?) are set for a track toggled."""

    kind: str  # "tracks", "albums", "artists" or "playlists"
    cause: Literal["toggle", "refresh"]
    item_id: str | None = None
    state: bool | None = None


# kind -> (attribute, model, client method)
_KINDS: dict[str, tuple[str, Any, str]] = {
    "tracks": ("tracks", Track, "get_all_favorite_tracks"),
    "albums": ("albums", Album, "get_all_favorite_albums"),
    "artists": ("artists", Artist, "get_all_favorite_artists"),
    "playlists": ("playlists", Playlist, "get_all_user_playlists"),
}


class Library:
    def __init__(
        self,
        client: QobuzClient,
        *,
        account_key: Callable[[], str],
        run_worker: Callable[..., Any],
        on_change: Callable[[Change], None],
    ) -> None:
        """``account_key``: whose library the disk copy is. ``run_worker``:
        Textual's, for the background refresh and the thread that writes the
        disk copy. ``on_change``: called when a list changed."""
        self.client = client
        self._account_key = account_key
        self._run_worker = run_worker
        self._on_change = on_change
        self.tracks: list[Track] | None = None
        self.track_ids: set[str] | None = None
        self.albums: list[Album] | None = None
        self.artists: list[Artist] | None = None
        self.playlists: list[Playlist] | None = None
        self._tracks_lock = asyncio.Lock()
        # One lock per list: they load in parallel.
        self._albums_lock = asyncio.Lock()
        self._artists_lock = asyncio.Lock()
        self._playlists_lock = asyncio.Lock()
        # Per list: toggles so far; a refresh started before one is dropped.
        self._edits: dict[str, int] = {}

    def reset(self) -> None:
        """Forget every list (logout, another account)."""
        self.tracks = self.track_ids = None
        self.albums = self.artists = None
        self.playlists = None

    # ── loading ──────────────────────────────────────────────────────────────

    async def ensure_tracks(self) -> list[Track]:
        """Favourite tracks, fetched exactly once and cached.

        Double-checked locking serialises concurrent first callers (the Tracks
        tab's list load, the mount-time heart warm, the Playback list refresh,
        each track-list builder) so the paginated favourites endpoint is hit
        once. Raises on fetch failure (without caching) so callers can retry."""
        if self.tracks is not None:
            return self.tracks
        async with self._tracks_lock:
            if self.tracks is None:
                self.tracks = await self._load("tracks")
            return self.tracks

    async def ensure_track_ids(self) -> set[str]:
        """Ids of the favourite tracks, derived from ``tracks``. A failed fetch
        gives an empty set (no hearts) but isn't cached, so the next call
        retries; caching it hid every favourite for the rest of the session
        after one failed request at startup."""
        if self.track_ids is None:
            try:
                tracks = await self.ensure_tracks()
            except Exception:
                return set()
            self.track_ids = {t.id for t in tracks}
        return self.track_ids

    async def ensure_albums(self) -> list[Album]:
        """Favourite albums, fetched once. Raises on failure (nothing cached)."""
        async with self._albums_lock:
            if self.albums is None:
                self.albums = await self._load("albums")
            return self.albums

    async def ensure_artists(self) -> list[Artist]:
        """Followed (favourite) artists, fetched once. Raises on failure."""
        async with self._artists_lock:
            if self.artists is None:
                self.artists = await self._load("artists")
            return self.artists

    async def ensure_playlists(self) -> list[Playlist]:
        """The user's playlists, fetched once. Raises on failure."""
        async with self._playlists_lock:
            if self.playlists is None:
                self.playlists = await self._load("playlists")
            return self.playlists

    async def warm(self) -> None:
        """Load the lists up front: track hearts, and the album and artist
        state the ``i`` menu labels its toggles with (it would otherwise wait
        on the first fetch before opening)."""
        await asyncio.gather(
            self.ensure_track_ids(),
            self.ensure_albums(),
            self.ensure_artists(),
            return_exceptions=True,  # a failed list is fetched again on first use
        )

    def is_favorite_album(self, album_id: str) -> bool:
        """From the cache: False until ``ensure_albums`` has loaded it."""
        return any(a.id == album_id for a in self.albums or ())

    def is_followed_artist(self, artist_id: str) -> bool:
        """From the cache: False until ``ensure_artists`` has loaded it."""
        return any(a.id == artist_id for a in self.artists or ())

    # ── toggling ─────────────────────────────────────────────────────────────

    async def toggle_track(self, track: Track) -> bool:
        """Add/remove a track from the favourites; the new state. Raises
        LibraryError (the state is unchanged) if it couldn't be done."""
        tid = str(track.id)
        ids = await self.ensure_track_ids()  # also loads tracks
        if self.track_ids is None:  # couldn't load them: don't guess the state
            raise LibraryError("Couldn't load favourites")
        try:
            if tid in ids:
                await self.client.set_favorite("track", tid, False)
                ids.discard(tid)
                if self.tracks is not None:
                    self.tracks = [t for t in self.tracks if t.id != tid]
                new_state = False
            else:
                await self.client.set_favorite("track", tid, True)
                ids.add(tid)
                if self.tracks is not None and not any(t.id == tid for t in self.tracks):
                    added = dataclasses.replace(track, favorited_at=int(time.time()))
                    self.tracks.append(added)
                new_state = True
        except Exception as e:
            raise LibraryError("Couldn't update favourite") from e
        self._edited("tracks")
        self._on_change(Change("tracks", "toggle", tid, new_state))
        return new_state

    async def toggle_album(self, album: Album) -> bool:
        """Add/remove an album; the new state. Raises LibraryError."""
        albums = await self.ensure_albums()
        on = not any(a.id == album.id for a in albums)
        try:
            await self.client.set_favorite("album", album.id, on)
        except QobuzError as e:
            raise LibraryError("Couldn't update favourite") from e
        if on:
            albums.append(dataclasses.replace(album, favorited_at=int(time.time())))
        else:
            albums[:] = [a for a in albums if a.id != album.id]
        self._edited("albums")
        self._on_change(Change("albums", "toggle", album.id, on))
        return on

    async def toggle_artist(self, artist: Artist) -> bool:
        """Follow/unfollow an artist; the new state. Raises LibraryError."""
        artists = await self.ensure_artists()
        on = not any(a.id == artist.id for a in artists)
        try:
            await self.client.set_favorite("artist", artist.id, on)
        except QobuzError as e:
            raise LibraryError("Couldn't update followed artists") from e
        if on:
            artists.append(dataclasses.replace(artist, favorited_at=int(time.time())))
        else:
            artists[:] = [a for a in artists if a.id != artist.id]
        self._edited("artists")
        self._on_change(Change("artists", "toggle", artist.id, on))
        return on

    # ── the disk copy and the background refresh ─────────────────────────────

    async def _load(self, kind: str) -> list:
        """The disk copy (and a refresh in the background), else a fetch.
        Nothing while logged out: after a logout the rebuilt views still ask
        (the queue's hearts), and a fetch would save a copy for nobody."""
        if not self.client.user_auth_token:
            raise QobuzError("Not logged in")
        since = library_cache.epoch()  # a logout during the awaits drops the save
        # In a thread: thousands of tracks take a moment to parse on a Pi.
        cached = await asyncio.to_thread(library_cache.load, kind, self._account_key())
        if cached is not None:
            self._run_worker(partial(self._refresh, kind), group="_refresh_library")
            return cached
        items = await self._fetch(kind)
        self._save(kind, items, since)
        return items

    async def _fetch(self, kind: str) -> list:
        _attr, model, method = _KINDS[kind]
        raw = await getattr(self.client, method)()
        return [model.from_api(r) for r in raw]

    def _save(self, kind: str, items: list | None = None, since: int | None = None) -> None:
        """Write the list (default: the current one) to the disk copy, in a
        thread. ``since``: the ``library_cache.epoch()`` when the list was asked
        for (default: now); an erase after it (logout) drops the save."""
        if items is None:
            items = getattr(self, _KINDS[kind][0])
        if items is None:
            return
        if since is None:
            since = library_cache.epoch()
        number = library_cache.ticket()  # decided now: an older save can't win
        self._run_worker(
            partial(self._write, kind, list(items), self._account_key(), since, number),
            group="_write_library",
            thread=True,
        )

    @staticmethod
    def _write(kind: str, items: list, account: str, since: int, number: int) -> None:
        with contextlib.suppress(Exception):  # a read-only disk: no copy, no harm
            # Dropped if logout erased the files meanwhile (`since`), or a
            # newer save of this list was written first (`number`).
            library_cache.save(kind, items, account, since, number)

    def _edited(self, kind: str) -> None:
        """A toggle changed the list: save it, and drop any refresh in flight
        (its answer may predate the change)."""
        self._edits[kind] = self._edits.get(kind, 0) + 1
        self._save(kind)

    async def _refresh(self, kind: str) -> None:
        edits, account = self._edits.get(kind, 0), self._account_key()
        since = library_cache.epoch()
        try:
            fresh = await self._fetch(kind)
        except Exception:
            return  # offline: the disk copy stays on screen
        attr = _KINDS[kind][0]
        current = getattr(self, attr)
        if current is None or self._edits.get(kind, 0) != edits or self._account_key() != account:
            return  # logged out, edited meanwhile (the edit saved it), or another account
        if fresh == current:
            return  # the disk copy is right too: nothing to write
        self._save(kind, fresh, since)
        setattr(self, attr, fresh)
        if kind == "tracks":
            self.track_ids = {t.id for t in fresh}
        self._on_change(Change(kind, "refresh"))
