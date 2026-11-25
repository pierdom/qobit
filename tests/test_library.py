"""Library without the app: a fake Qobuz client, a worker runner that keeps what
it is given, and the disk copy redirected by conftest."""

from __future__ import annotations

import asyncio

import pytest

from qobit import library_cache
from qobit.library import Change, Library, LibraryError
from qobit.qobuz.client import QobuzError
from qobit.qobuz.models import Album, Artist, Track

ACCOUNT = "me"


def _raw_track(i: int) -> dict:
    return {"id": i, "title": f"T{i}", "performer": {"name": "A"}, "album": {"title": "B"}}


def _track(i: int) -> Track:
    return Track.from_api(_raw_track(i))


def _album(i: int) -> Album:
    return Album(id=f"a{i}", title=f"Album {i}", artist="A", year=2020, tracks_count=1)


def _artist(i: int) -> Artist:
    return Artist(id=f"r{i}", name=f"Artist {i}")


class Client:
    user_auth_token = "token"

    def __init__(self, tracks=(1, 2, 3)):
        self.tracks = list(tracks)
        self.fetches: list[str] = []
        self.favorites: list[tuple[str, str, bool]] = []
        self.fail_fetch = False
        self.fail_set = False
        self.gate: asyncio.Event | None = None

    async def get_all_favorite_tracks(self):
        self.fetches.append("tracks")
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_fetch:
            raise QobuzError("down")
        return [_raw_track(i) for i in self.tracks]

    async def get_all_favorite_albums(self):
        self.fetches.append("albums")
        return []

    async def get_all_favorite_artists(self):
        self.fetches.append("artists")
        return []

    async def get_all_user_playlists(self):
        self.fetches.append("playlists")
        return []

    async def set_favorite(self, kind, item_id, on):
        if self.fail_set:
            raise QobuzError("no")
        self.favorites.append((kind, item_id, on))


class Workers:
    """run_worker: keeps the work; ``settle`` runs it (threads inline)."""

    def __init__(self):
        self.queue: list[tuple[str, bool, object]] = []

    def __call__(self, work, *, group, exclusive=False, thread=False):
        self.queue.append((group, thread, work))

    async def settle(self):
        while self.queue:
            _, _, work = self.queue.pop(0)
            result = work()
            if asyncio.iscoroutine(result):
                await result


def _make(client=None):
    client = client or Client()
    workers, changes = Workers(), []
    lib = Library(
        client,  # type: ignore[arg-type]
        account_key=lambda: ACCOUNT,
        run_worker=workers,
        on_change=changes.append,
    )
    return lib, client, workers, changes


def _run(coro):
    return asyncio.run(coro)


# ── loading ──────────────────────────────────────────────────────────────


def test_the_first_callers_share_one_fetch():
    async def body():
        lib, client, workers, _ = _make()
        client.gate = asyncio.Event()
        first = asyncio.create_task(lib.ensure_tracks())
        second = asyncio.create_task(lib.ensure_tracks())
        await asyncio.sleep(0.01)
        client.gate.set()
        a, b = await first, await second
        assert a is b and [t.id for t in a] == ["1", "2", "3"]
        assert client.fetches == ["tracks"]
        await workers.settle()
        assert library_cache.load("tracks", ACCOUNT) is not None  # and saved to disk

    _run(body())


def test_a_failed_fetch_is_not_cached_and_ids_are_empty_meanwhile():
    async def body():
        lib, client, _, _ = _make()
        client.fail_fetch = True
        assert await lib.ensure_track_ids() == set()
        assert lib.tracks is None and lib.track_ids is None
        client.fail_fetch = False
        assert await lib.ensure_track_ids() == {"1", "2", "3"}

    _run(body())


def test_nothing_is_fetched_while_logged_out():
    async def body():
        lib, client, _, _ = _make()
        client.user_auth_token = ""
        with pytest.raises(QobuzError):
            await lib.ensure_tracks()
        assert client.fetches == []

    _run(body())


def test_reset_forgets_every_list():
    async def body():
        lib, _, _, _ = _make()
        await lib.ensure_tracks()
        await lib.ensure_track_ids()
        lib.reset()
        assert lib.tracks is None and lib.track_ids is None and lib.albums is None

    _run(body())


# ── the disk copy and the refresh ────────────────────────────────────────


def _seed_disk(ids):
    library_cache.save(
        "tracks", [_track(i) for i in ids], ACCOUNT, library_cache.epoch(), library_cache.ticket()
    )


def test_the_disk_copy_is_returned_at_once_and_a_refresh_follows():
    async def body():
        _seed_disk([1, 2])
        lib, client, workers, changes = _make(Client(tracks=[1, 2, 3]))
        got = await lib.ensure_tracks()
        assert [t.id for t in got] == ["1", "2"] and client.fetches == []
        assert [g for g, _, _ in workers.queue] == ["_refresh_library"]
        await workers.settle()
        assert [t.id for t in lib.tracks] == ["1", "2", "3"]
        assert lib.track_ids == {"1", "2", "3"}  # kept in step with the list
        assert changes == [Change("tracks", "refresh")]

    _run(body())


def test_an_unchanged_refresh_changes_nothing_and_writes_nothing():
    async def body():
        _seed_disk([1, 2, 3])
        lib, _, workers, changes = _make(Client(tracks=[1, 2, 3]))
        await lib.ensure_tracks()
        await workers.settle()
        assert changes == [] and workers.queue == []  # no change, no save

    _run(body())


def test_a_failed_refresh_keeps_the_disk_copy():
    async def body():
        _seed_disk([1, 2])
        client = Client()
        client.fail_fetch = True
        lib, _, workers, changes = _make(client)
        await lib.ensure_tracks()
        await workers.settle()
        assert [t.id for t in lib.tracks] == ["1", "2"] and changes == []

    _run(body())


def test_a_toggle_during_the_refresh_wins():
    async def body():
        _seed_disk([1, 2])
        client = Client(tracks=[1, 2])
        lib, _, workers, changes = _make(client)
        await lib.ensure_tracks()
        await lib.ensure_track_ids()
        refresh = workers.queue.pop(0)  # the refresh has started...
        client.gate = asyncio.Event()
        task = asyncio.ensure_future(refresh[2]())
        await asyncio.sleep(0.01)
        await lib.toggle_track(_track(9))  # ...the user toggles meanwhile
        client.gate.set()
        await task
        assert [t.id for t in lib.tracks] == ["1", "2", "9"]  # the stale answer is dropped
        assert [c.cause for c in changes] == ["toggle"]

    _run(body())


def test_another_account_during_the_refresh_drops_it():
    async def body():
        _seed_disk([1])
        account = {"name": ACCOUNT}
        client = Client(tracks=[1, 5])
        workers, changes = Workers(), []
        lib = Library(
            client,  # type: ignore[arg-type]
            account_key=lambda: account["name"],
            run_worker=workers,
            on_change=changes.append,
        )
        await lib.ensure_tracks()
        client.gate = asyncio.Event()
        task = asyncio.ensure_future(workers.queue.pop(0)[2]())  # the refresh starts...
        await asyncio.sleep(0.01)
        account["name"] = "someone else"  # ...another account logs in meanwhile
        client.gate.set()
        await task
        assert [t.id for t in lib.tracks] == ["1"] and changes == []

    _run(body())


# ── toggling ─────────────────────────────────────────────────────────────


def test_favouriting_a_track_updates_the_lists_the_disk_and_tells_the_views():
    async def body():
        lib, client, workers, changes = _make()
        assert await lib.toggle_track(_track(7)) is True
        assert client.favorites == [("track", "7", True)]
        assert "7" in lib.track_ids and [t.id for t in lib.tracks][-1] == "7"
        assert lib.tracks[-1].favorited_at is not None
        assert changes == [Change("tracks", "toggle", "7", True)]
        await workers.settle()
        assert "7" in [t.id for t in library_cache.load("tracks", ACCOUNT)]

    _run(body())


def test_unfavouriting_a_track_removes_it():
    async def body():
        lib, client, _, changes = _make()
        assert await lib.toggle_track(_track(2)) is False
        assert client.favorites == [("track", "2", False)]
        assert "2" not in lib.track_ids and [t.id for t in lib.tracks] == ["1", "3"]
        assert changes == [Change("tracks", "toggle", "2", False)]

    _run(body())


def test_a_track_toggle_that_fails_changes_nothing():
    async def body():
        lib, client, _, changes = _make()
        await lib.ensure_track_ids()
        client.fail_set = True
        with pytest.raises(LibraryError, match="Couldn't update favourite"):
            await lib.toggle_track(_track(9))
        assert "9" not in lib.track_ids and changes == []

    _run(body())


def test_a_toggle_that_cannot_load_the_favourites_says_so():
    async def body():
        lib, client, _, _ = _make()
        client.fail_fetch = True
        with pytest.raises(LibraryError, match="Couldn't load favourites"):
            await lib.toggle_track(_track(9))
        assert client.favorites == []  # never guessed the state

    _run(body())


def test_albums_and_artists_toggle_both_ways_and_answer_the_is_questions():
    async def body():
        lib, client, _, changes = _make()
        assert await lib.toggle_album(_album(1)) is True
        assert lib.is_favorite_album("a1") and not lib.is_favorite_album("a2")
        assert await lib.toggle_album(_album(1)) is False
        assert not lib.is_favorite_album("a1")
        assert await lib.toggle_artist(_artist(1)) is True
        assert lib.is_followed_artist("r1")
        assert await lib.toggle_artist(_artist(1)) is False
        assert client.favorites == [
            ("album", "a1", True),
            ("album", "a1", False),
            ("artist", "r1", True),
            ("artist", "r1", False),
        ]
        assert [(c.kind, c.state) for c in changes] == [
            ("albums", True),
            ("albums", False),
            ("artists", True),
            ("artists", False),
        ]

    _run(body())


def test_an_album_or_artist_toggle_that_fails_changes_nothing():
    async def body():
        lib, client, _, changes = _make()
        client.fail_set = True
        with pytest.raises(LibraryError, match="Couldn't update favourite"):
            await lib.toggle_album(_album(1))
        with pytest.raises(LibraryError, match="Couldn't update followed artists"):
            await lib.toggle_artist(_artist(1))
        assert lib.albums == [] and lib.artists == [] and changes == []

    _run(body())
