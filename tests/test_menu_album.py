"""The `i` menu in an album: it offered only "Add album to favourites"
because the opened tracklist had focus but no row selected; and an album on
its own (a grid card) had no way to its artist."""

from __future__ import annotations

import asyncio

import pytest
from album_pages import ALBUM, open_album, stub_album_client, wait_until

from qobit.ui.screens.artist_detail import ArtistScreen
from qobit.ui.screens.context_menu import ContextMenu, MenuOption


def _labels(menu: ContextMenu) -> list[str]:
    return [str(label.content) for label in menu.query("MenuOption Label")]


async def _menu(app, pilot) -> list[str]:
    await pilot.press("i")
    await wait_until(pilot, lambda: isinstance(app.screen, ContextMenu))
    return _labels(app.screen)


@pytest.mark.parametrize("where", ["page", "albums", "artists"])
def test_an_opened_album_selects_a_track_and_offers_its_artist(ui_app, where):
    stub_album_client(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            panel, _ = await open_album(ui_app, pilot, where)
            lv = panel.query_one(".detail-tracklist")
            await wait_until(pilot, lambda: lv.index == 0 and ui_app.focused is lv)
            labels = " ".join(await _menu(ui_app, pilot))
            for option in ("Play next", "Go to artist", "favourites"):
                assert option in labels

    asyncio.run(run())


def test_album_card_menu_goes_to_the_artist(ui_app):
    stub_album_client(ui_app)

    async def favourites():
        return [ALBUM]

    ui_app._client.get_all_favorite_albums = favourites

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.action_switch_tab("albums")
            grid = ui_app.screen.query_one("#fav-albums-grid")
            await wait_until(pilot, lambda: grid.selected_card() is not None)
            await _menu(ui_app, pilot)
            options = list(ui_app.screen.query(MenuOption))
            [go] = [o for o in options if o.action_id == "album_artist"]
            await pilot.press(str(go._number))  # its number key, as a user would
            await wait_until(pilot, lambda: isinstance(ui_app.screen, ArtistScreen))

    asyncio.run(run())
