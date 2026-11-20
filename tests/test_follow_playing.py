"""In shuffle the playing track lands anywhere in a long list, mostly in rows
that aren't mounted yet (a list mounts 100 and the rest as the cursor nears
them). The cursor used to stay on the old row and never follow again; now it
mounts through the playing row, keeps following until you move it, and `p`
brings it back."""

from __future__ import annotations

import asyncio

from album_pages import wait_until

from qobit.qobuz.models import Track

FAVS = [
    {"id": i, "title": f"T{i}", "performer": {"name": "A"}, "album": {"title": "B"}}
    for i in range(300)
]


def _track(i: int) -> Track:
    return Track.from_api(FAVS[i])


def _stub(app) -> None:
    """The favourites, before the app starts (it loads them at startup)."""

    async def tracks():
        return FAVS

    app._client.get_all_favorite_tracks = tracks


async def _open_tracks(app, pilot):
    app.action_switch_tab("tracks")
    lv = app.screen.query_one("#fav-tracks")
    await wait_until(pilot, lambda: lv.highlighted_child and app.focused is lv, timeout=15)
    return lv


def _run(body):
    asyncio.run(body())


def test_the_cursor_follows_the_playing_track_into_rows_not_mounted_yet(ui_app):
    _stub(ui_app)

    async def body():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            lv = await _open_tracks(ui_app, pilot)
            assert len(lv.children) == 100
            ui_app.now_playing = _track(0)
            await wait_until(pilot, lambda: lv.index == 0)
            for i in (40, 250, 5, 150, 6, 299):  # the run that used to freeze at 40
                ui_app.now_playing = _track(i)
                await wait_until(pilot, lambda i=i: lv.index == i, timeout=8)
            assert len(lv.children) >= 300  # mounted through the last one

    _run(body)


def test_a_cursor_you_moved_stays_where_you_put_it(ui_app):
    _stub(ui_app)

    async def body():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            lv = await _open_tracks(ui_app, pilot)
            ui_app.now_playing = _track(0)
            await wait_until(pilot, lambda: lv.index == 0)
            lv.index = 3  # browsing
            ui_app.now_playing = _track(250)
            await pilot.pause(0.6)
            assert lv.index == 3 and len(lv.children) == 100  # nothing mounted for it

    _run(body)


def test_p_brings_the_cursor_to_the_playing_track_and_following_resumes(ui_app):
    _stub(ui_app)

    async def body():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            lv = await _open_tracks(ui_app, pilot)
            ui_app.now_playing = _track(0)
            await wait_until(pilot, lambda: lv.index == 0)
            lv.index = 3
            ui_app.now_playing = _track(250)
            await pilot.pause(0.3)
            assert lv.index == 3
            assert ui_app.check_action("go_to_playing", ())
            await pilot.press("p")
            await wait_until(pilot, lambda: lv.index == 250, timeout=8)
            ui_app.now_playing = _track(7)  # and it follows again
            await wait_until(pilot, lambda: lv.index == 7)

    _run(body)


def test_the_border_says_where_the_playing_track_is(ui_app):
    _stub(ui_app)

    async def body():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            lv = await _open_tracks(ui_app, pilot)
            ui_app.now_playing = _track(250)
            box = ui_app.screen.query_one("#tracks-container")
            await wait_until(pilot, lambda: "▶ 251" in str(box.border_subtitle), timeout=8)
            assert lv.index == 250 or lv.index is not None

    _run(body)


def test_p_says_so_when_the_playing_track_is_not_in_the_list(ui_app):
    _stub(ui_app)

    async def body():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            await _open_tracks(ui_app, pilot)
            ui_app.now_playing = Track(
                id="elsewhere", title="x", artist="y", album="z", album_id="1", duration=1
            )
            await pilot.pause(0.3)
            await pilot.press("p")
            await wait_until(pilot, lambda: "isn't in this list" in str(ui_app.status_msg))

    _run(body)


def test_p_is_off_when_nothing_plays_or_on_a_tab_with_no_such_list(ui_app):
    _stub(ui_app)

    async def body():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            await _open_tracks(ui_app, pilot)
            assert not ui_app.check_action("go_to_playing", ())  # nothing playing
            ui_app.now_playing = _track(1)
            await pilot.pause(0.2)
            assert ui_app.check_action("go_to_playing", ())
            ui_app.action_switch_tab("queue")
            await wait_until(pilot, lambda: ui_app.base_tab() == "queue")
            await pilot.pause(0.3)
            assert not ui_app.check_action("go_to_playing", ())

    _run(body)


def test_following_survives_a_track_from_another_list(ui_app):
    _stub(ui_app)

    async def body():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            lv = await _open_tracks(ui_app, pilot)
            ui_app.now_playing = _track(0)
            await wait_until(pilot, lambda: lv.index == 0)
            ui_app.now_playing = Track(  # a track this list doesn't have
                id="elsewhere", title="x", artist="y", album="z", album_id="1", duration=1
            )
            await pilot.pause(0.3)
            assert lv.index == 0  # nothing to follow to
            ui_app.now_playing = _track(7)  # back in this list: the cursor, untouched, follows
            await wait_until(pilot, lambda: lv.index == 7)

    _run(body)
