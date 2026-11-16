"""`/` filter in the Tracks and Albums tabs (Artists and Playlists share the
same TypeToFilter). While typing, `i` was just a letter and Enter played the
highlighted track, replacing the queue: no way to act on a result. Enter now
ends the typing and keeps the results."""

from __future__ import annotations

import asyncio

from album_pages import ALBUM, stub_album_client, wait_until

from qobit.qobuz.models import Track
from qobit.ui.screens.context_menu import ContextMenu, MenuOption


def _fav(id_: int, title: str, artist: str, album: str) -> dict:
    return {"id": id_, "title": title, "performer": {"name": artist}, "album": {"title": album}}


FAVS = [
    _fav(1, "So What", "Miles Davis", "Kind of Blue"),
    _fav(2, "Halo", "Beyoncé", "I Am"),
    _fav(3, "Blue in Green", "Miles Davis", "Kind of Blue"),
]


def _favourites(app) -> None:
    async def tracks():
        return FAVS

    async def albums():
        return [ALBUM]

    app._client.get_all_favorite_tracks = tracks
    app._client.get_all_favorite_albums = albums


def test_enter_keeps_the_filtered_tracks_and_i_opens_the_menu(ui_app, monkeypatch):
    stub_album_client(ui_app)
    _favourites(ui_app)
    played: list[Track] = []
    monkeypatch.setattr(ui_app, "play_track", lambda t, *a, **k: played.append(t))

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.action_switch_tab("tracks")
            view = ui_app.screen.query_one("#view-tracks")
            lv = view.query_one("#fav-tracks")
            # The tab focuses its list a refresh after activating; typing
            # before that went to the tab bar.
            await wait_until(pilot, lambda: lv.highlighted_child and ui_app.focused is lv)
            await pilot.press("slash", *"miles")

            def filtered() -> bool:  # the debounced re-render, cursor on a match
                row = lv.highlighted_child
                return (
                    row is not None
                    and getattr(row, "track", None) is not None
                    and (row.track.artist == "Miles Davis" and len(lv.children) == 2)
                )

            await wait_until(pilot, filtered)
            assert view._filter_active and view._filter_query == "miles"
            assert "⏎ done" in str(view.query_one("#tracks-container").border_subtitle)

            await pilot.press("enter")
            await pilot.pause(0.3)
            assert played == []  # Enter ended the typing, it didn't play
            assert not view._filter_active and view._filter_query == "miles"
            subtitle = str(view.query_one("#tracks-container").border_subtitle)
            assert subtitle.startswith("⌕ miles")

            await pilot.press("i")
            await wait_until(pilot, lambda: isinstance(ui_app.screen, ContextMenu))
            actions = {o.action_id for o in ui_app.screen.query(MenuOption)}
            assert {"play_next", "add_queue", "artist"} <= actions
            await pilot.press("escape")
            await wait_until(pilot, lambda: not isinstance(ui_app.screen, ContextMenu))

            await pilot.press("enter")  # now Enter plays, as on any list
            await wait_until(pilot, lambda: played)
            assert [t.title for t in played] in (["So What"], ["Blue in Green"])

            await pilot.press("escape")  # and Escape clears the filter
            await wait_until(pilot, lambda: view._filter_query == "")

    asyncio.run(run())


def test_typing_still_takes_i_as_a_letter(ui_app):
    stub_album_client(ui_app)
    _favourites(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.action_switch_tab("tracks")
            view = ui_app.screen.query_one("#view-tracks")
            lv = view.query_one("#fav-tracks")
            await wait_until(pilot, lambda: lv.highlighted_child and ui_app.focused is lv)
            await pilot.press("slash", *"mi")
            await wait_until(pilot, lambda: view._filter_query == "mi")
            await pilot.pause(0.3)  # long enough for a menu to have opened
            assert not isinstance(ui_app.screen, ContextMenu)

    asyncio.run(run())


def test_album_grid_filter_then_menu(ui_app):
    stub_album_client(ui_app)
    _favourites(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.action_switch_tab("albums")
            view = ui_app.screen.query_one("#view-albums")
            grid = view.query_one("#fav-albums-grid")
            await wait_until(pilot, lambda: grid.selected_card() and ui_app.focused is grid)
            await pilot.press("slash", *"kind", "enter")
            await wait_until(pilot, lambda: not view._filter_active and grid.selected_card())
            assert not view._album_view_active  # Enter didn't open the album
            assert view._filter_query == "kind"
            await pilot.press("i")
            await wait_until(pilot, lambda: isinstance(ui_app.screen, ContextMenu))

    asyncio.run(run())
