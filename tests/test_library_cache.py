"""The library on disk (library_cache): the Tracks, Albums, Artists and
Playlists tabs show the last fetched lists at once, then a background fetch
replaces them only if they changed, keeping the cursor on the same item.
Fetching every list at each start took seconds (worse on a Raspberry Pi)."""

from __future__ import annotations

import asyncio

from album_pages import ALBUM, wait_until

from qobit import account_data, library_cache, store
from qobit.qobuz.models import Album, Playlist, Track

ACCOUNT = ""  # the stub client has no account name


def _track(i: int) -> dict:
    return {"id": i, "title": f"T{i}", "performer": {"name": "A"}, "album": {"title": "B"}}


def _tracks(ids) -> list[Track]:
    return [Track.from_api(_track(i)) for i in ids]


# ── the file ──────────────────────────────────────────────────────────────


def test_each_kind_reads_back_as_saved():
    album = Album.from_api(ALBUM)  # with nested tracks
    playlist = Playlist.from_api({"id": "p", "name": "P", "tracks_count": 1, "owner": {}})
    lists = {"tracks": _tracks([1, 2]), "albums": [album], "playlists": [playlist]}
    for kind, items in lists.items():
        library_cache.save(kind, items, "me")
    library_cache.forget()  # read from the file, not memory
    for kind, items in lists.items():
        assert library_cache.load(kind, "me") == items
    assert library_cache.load("artists", "me") is None  # never saved


def test_another_account_or_format_reads_as_empty():
    library_cache.save("tracks", _tracks([1]), "me")
    assert library_cache.load("tracks", "someone else") is None
    library_cache.forget()
    data = store.load(library_cache.FILE)
    store.save(library_cache.FILE, {**data, "version": library_cache.VERSION + 1})
    library_cache.forget()
    assert library_cache.load("tracks", "me") is None


def test_entries_from_older_fields_read_as_empty():
    store.save(
        library_cache.FILE,
        {"version": library_cache.VERSION, "account": "me", "tracks": [{"id": "1", "nope": 2}]},
    )
    assert library_cache.load("tracks", "me") is None


def test_logout_erases_it():
    library_cache.save("tracks", _tracks([1]), "me")
    account_data.erase_account_data()
    assert not (store.DATA_DIR / library_cache.FILE).exists()
    assert library_cache.load("tracks", "me") is None


# ── the app ───────────────────────────────────────────────────────────────


def _slow_fetch(app, ids, release: asyncio.Event | None = None, fail: bool = False):
    async def fetch():
        if release is not None:
            await release.wait()
        if fail:
            raise RuntimeError("offline")
        return [_track(i) for i in ids]

    app._client.get_all_favorite_tracks = fetch


def test_tracks_show_from_disk_then_update_keeping_the_cursor(ui_app):
    library_cache.save("tracks", _tracks(range(1, 6)), ACCOUNT)
    release = asyncio.Event()
    # Track 3 stays, 2 is gone, 9 is new.
    _slow_fetch(ui_app, [9, 1, 3, 4, 5], release)

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.action_switch_tab("tracks")
            view = ui_app.screen.query_one("#view-tracks")
            lv = view.query_one("#fav-tracks")
            # From disk, while the fetch is still waiting.
            await wait_until(pilot, lambda: len(lv.rows) == 5 and lv.highlighted_child)
            await wait_until(pilot, lambda: ui_app.focused is lv)
            await pilot.press("down", "down")  # on track 3
            await wait_until(pilot, lambda: lv.highlighted_child.track.id == "3")

            release.set()
            await wait_until(pilot, lambda: [r.track.id for r in lv.rows][:2] == ["9", "1"])
            await wait_until(
                pilot, lambda: getattr(lv.highlighted_child, "track", None) is not None
            )
            await wait_until(pilot, lambda: lv.highlighted_child.track.id == "3")
            assert ui_app._fav_ids == {"9", "1", "3", "4", "5"}
            library_cache.forget()
            saved = library_cache.load("tracks", ACCOUNT)
            assert [t.id for t in saved] == ["9", "1", "3", "4", "5"]

    asyncio.run(run())


def test_an_unchanged_list_is_not_redrawn(ui_app):
    library_cache.save("tracks", _tracks([1, 2, 3]), ACCOUNT)
    release = asyncio.Event()
    _slow_fetch(ui_app, [1, 2, 3], release)

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.action_switch_tab("tracks")
            view = ui_app.screen.query_one("#view-tracks")
            lv = view.query_one("#fav-tracks")
            await wait_until(pilot, lambda: len(lv.rows) == 3)
            rows, version = list(lv.rows), view._render_version
            release.set()
            await pilot.pause(0.5)
            assert view._render_version == version and list(lv.rows) == rows

    asyncio.run(run())


def test_offline_keeps_the_disk_copy(ui_app):
    library_cache.save("tracks", _tracks([1, 2]), ACCOUNT)
    _slow_fetch(ui_app, [], fail=True)

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.action_switch_tab("tracks")
            lv = ui_app.screen.query_one("#fav-tracks")
            await wait_until(pilot, lambda: len(lv.rows) == 2)
            await pilot.pause(0.3)
            assert [r.track.id for r in lv.rows] == ["1", "2"]

    asyncio.run(run())


def test_a_toggle_during_the_refresh_wins(ui_app):
    """The refresh began before the toggle: its list doesn't have it."""
    library_cache.save("tracks", _tracks([1, 2]), ACCOUNT)
    release = asyncio.Event()
    _slow_fetch(ui_app, [1, 2], release)

    async def set_favorite(*_a):
        pass

    ui_app._client.set_favorite = set_favorite

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            await wait_until(pilot, lambda: ui_app._fav_tracks is not None)
            await ui_app.toggle_favorite(_tracks([7])[0])
            release.set()
            await pilot.pause(0.5)
            assert [t.id for t in ui_app._fav_tracks] == ["1", "2", "7"]
            library_cache.forget()
            await wait_until(
                pilot,
                lambda: (
                    [t.id for t in library_cache.load("tracks", ACCOUNT) or ()] == ["1", "2", "7"]
                ),
            )

    asyncio.run(run())


def test_albums_grid_keeps_its_cursor(ui_app):
    albums = [dict(ALBUM, id=f"a{i}", title=f"Album {i}") for i in range(6)]
    library_cache.save("albums", [Album.from_api(a) for a in albums], ACCOUNT)
    release = asyncio.Event()

    async def fetch():
        await release.wait()
        return [dict(ALBUM, id="new", title="New"), *albums]

    ui_app._client.get_all_favorite_albums = fetch

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.action_switch_tab("albums")
            grid = ui_app.screen.query_one("#fav-albums-grid")
            await wait_until(pilot, lambda: grid.selected_card() and ui_app.focused is grid)
            await pilot.press("right", "right")
            card = grid.selected_card()
            release.set()
            await wait_until(pilot, lambda: len(grid._visible_cards()) == 7)
            keep = card._album.id
            await wait_until(
                pilot, lambda: getattr(grid.selected_card(), "_album", None) is not None
            )
            await wait_until(pilot, lambda: grid.selected_card()._album.id == keep)

    asyncio.run(run())


# ── erasing it ────────────────────────────────────────────────────────────


def test_a_save_on_its_way_doesnt_bring_it_back():
    """A save running in a thread when logout erased the file would write
    the old account's library back."""
    since = library_cache.epoch()
    library_cache.save("tracks", _tracks([1]), "me", since)
    account_data.erase_account_data()
    library_cache.save("tracks", _tracks([1, 2]), "me", since)  # the late one
    assert not (store.DATA_DIR / library_cache.FILE).exists()


def test_logout_in_the_app_wipes_it(ui_app):
    library_cache.save("tracks", _tracks([1, 2]), ACCOUNT)
    _slow_fetch(ui_app, [1, 2, 3])

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.request_login = lambda *_a, **_k: None  # the stub can't log in
            await wait_until(pilot, lambda: ui_app._fav_tracks is not None)
            ui_app._save_library("tracks")  # a save on its way, then logout
            await ui_app.action_logout()
            await pilot.pause(0.5)
            assert not (store.DATA_DIR / library_cache.FILE).exists()
            assert ui_app._fav_tracks is None and ui_app._user_playlists is None
            assert library_cache.load("tracks", ACCOUNT) is None

    asyncio.run(run())


def _clear_cache(capsys, *flags: str) -> str:
    import argparse

    from qobit.__main__ import cmd_clear_cache

    args = argparse.Namespace(covers="--covers" in flags, library="--library" in flags)
    assert cmd_clear_cache(args) == 0
    return capsys.readouterr().out


def _seed_caches():
    from qobit.ui import _images

    library_cache.save("tracks", _tracks([1]), "me")
    covers = _images._cache_dir()
    covers.mkdir(parents=True, exist_ok=True)
    (covers / "a.jpg").write_bytes(b"1")
    return store.DATA_DIR / library_cache.FILE, covers / "a.jpg"


def test_clear_cache_deletes_covers_and_library(capsys):
    library, cover = _seed_caches()
    out = _clear_cache(capsys)
    assert not library.exists() and not cover.exists()
    assert "Cleared 1 cached cover(s)" in out
    assert "Cleared the saved copy of the library" in out
    assert library_cache.load("tracks", "me") is None  # not from memory either
    assert "No saved copy of the library" in _clear_cache(capsys)


def test_clear_cache_one_of_them(capsys):
    library, cover = _seed_caches()
    _clear_cache(capsys, "--library")
    assert not library.exists() and cover.exists()
    library, cover = _seed_caches()
    _clear_cache(capsys, "--covers")
    assert library.exists() and not cover.exists()
