"""The library on disk (library_cache): the Tracks, Albums, Artists and
Playlists tabs show the last fetched lists at once, then a background fetch
replaces them only if they changed, keeping the cursor on the same item.
Fetching every list at each start took seconds (worse on a Raspberry Pi)."""

from __future__ import annotations

import asyncio
import json

from album_pages import ALBUM, wait_until

from qobit import account_data, library_cache
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
    for kind, items in lists.items():
        assert library_cache.load(kind, "me") == items
    assert library_cache.load("artists", "me") is None  # never saved


def test_another_account_or_format_reads_as_empty():
    library_cache.save("tracks", _tracks([1]), "me")
    assert library_cache.load("tracks", "someone else") is None
    file = library_cache.path("tracks")
    file.write_text(json.dumps({**json.loads(file.read_text()), "version": 99}))
    assert library_cache.load("tracks", "me") is None


def test_entries_from_older_fields_read_as_empty():
    library_cache.save("tracks", [], "me")
    data = {"version": library_cache.VERSION, "account": "me", "items": [{"id": "1", "nope": 2}]}
    library_cache.path("tracks").write_text(json.dumps(data))
    assert library_cache.load("tracks", "me") is None


def test_an_older_save_finishing_last_is_dropped():
    """Saves run in a thread pool: the older list must not win."""
    first, second = library_cache.ticket(), library_cache.ticket()
    library_cache.save("tracks", _tracks([1, 2]), "me", number=second)
    library_cache.save("tracks", _tracks([1]), "me", number=first)
    assert [t.id for t in library_cache.load("tracks", "me")] == ["1", "2"]


def test_logout_erases_it():
    library_cache.save("tracks", _tracks([1]), "me")
    account_data.erase_account_data()
    assert not (library_cache.path("tracks")).exists()
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
            # Written by a thread worker: poll.
            await wait_until(
                pilot,
                lambda: (
                    [t.id for t in library_cache.load("tracks", ACCOUNT) or ()]
                    == ["9", "1", "3", "4", "5"]
                ),
            )

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
            writes = []
            save = library_cache.save
            library_cache.save = lambda *a, **k: (writes.append(a[0]), save(*a, **k))
            try:
                release.set()
                await pilot.pause(0.5)
            finally:
                library_cache.save = save
            assert view._render_version == version and list(lv.rows) == rows
            assert "tracks" not in writes  # the disk copy is right: not rewritten

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
    assert not (library_cache.path("tracks")).exists()


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
            assert not (library_cache.path("tracks")).exists()
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
    return library_cache.path("tracks"), covers / "a.jpg"


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


def test_clear_cache_leaves_the_library_while_qobit_runs(capsys, monkeypatch):
    """The running app holds the library in memory and would write it back
    on the next change."""
    import argparse

    import qobit.__main__ as cli

    library, _cover = _seed_caches()
    monkeypatch.setattr(cli, "tui_running", lambda: True)
    assert cli.cmd_clear_cache(argparse.Namespace(covers=False, library=True)) == 1
    assert library.exists()
    assert "quit it first" in capsys.readouterr().err


def test_reselecting_mounts_through_the_row_not_the_whole_list():
    from textual.app import App
    from textual.widgets import Label, ListItem

    from qobit.ui.widgets.lists import _PAGE_ROWS, PagedListView

    class _App(App):
        def compose(self):
            yield PagedListView()

    async def run():
        app = _App()
        async with app.run_test(size=(60, 20)) as pilot:
            lv = app.query_one(PagedListView)
            rows = [ListItem(Label(str(i)), name=str(i)) for i in range(1000)]
            await lv.set_rows(rows)
            assert await lv.select_where(lambda r: r.name == "450")
            await pilot.pause()
            assert lv.index == 450 and lv.highlighted_child.name == "450"
            # Through it and a page, plus the page the scroll's look-ahead
            # adds: not all 1000.
            assert len(lv.children) <= 450 + 2 * _PAGE_ROWS
            assert len(lv.rows) == 1000

    asyncio.run(run())
