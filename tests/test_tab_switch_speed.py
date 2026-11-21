"""Switching tabs on a slow machine took seconds. Most of it was restyling:
Textual restyles every widget inside a widget whose focus or classes change
(each row and label of a 200-row list, twice per switch), and its Footer
rebuilt all its keys four or five times per switch."""

from __future__ import annotations

import asyncio

from album_pages import quiet, wait_until
from textual.color import Color

from qobit.ui.nav import QobitFooter

FAVS = [
    {"id": i, "title": f"T{i}", "performer": {"name": "A"}, "album": {"title": "B"}}
    for i in range(150)
]


def _favourites(app) -> None:
    async def tracks():
        return FAVS

    app._client.get_all_favorite_tracks = tracks


def test_switching_restyles_the_lists_not_their_rows(ui_app, monkeypatch):
    _favourites(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.action_switch_tab("tracks")
            lv = ui_app.screen.query_one("#fav-tracks")
            # The first load: slow on a loaded runner.
            await wait_until(
                pilot, lambda: lv.highlighted_child and ui_app.focused is lv, timeout=15
            )
            await pilot.pause(0.2)

            restyled: list[object] = []
            update_nodes = ui_app.stylesheet.update_nodes

            def spy(nodes, *a, **k):
                nodes = list(nodes)
                restyled.extend(nodes)
                return update_nodes(nodes, *a, **k)

            monkeypatch.setattr(ui_app.stylesheet, "update_nodes", spy)
            ui_app.action_switch_tab("queue")
            await pilot.pause(0.3)
            ui_app.action_switch_tab("tracks")
            await wait_until(pilot, lambda: ui_app.focused is lv)
            await pilot.pause(0.3)
            rows = set(lv.children)
            # Only the highlighted row, of 100 mounted, was restyled.
            assert sum(1 for n in restyled if n in rows) <= 4

    asyncio.run(run())


def test_the_highlighted_row_still_follows_the_focus(ui_app):
    _favourites(ui_app)

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            ui_app.action_switch_tab("tracks")
            lv = ui_app.screen.query_one("#fav-tracks")
            # The first load: slow on a loaded runner.
            await wait_until(
                pilot, lambda: lv.highlighted_child and ui_app.focused is lv, timeout=15
            )
            row = lv.highlighted_child
            variables = ui_app.theme_variables
            select, dim = (Color.parse(variables[v]) for v in ("qobit-select", "qobit-select-dim"))
            await wait_until(pilot, lambda: row.styles.background == select)
            ui_app.screen.query_one("#nav-tabs").focus()
            await wait_until(pilot, lambda: row.styles.background == dim)
            # The panel's border follows too (its own class, restyled alone).
            container = ui_app.screen.query_one("#tracks-container")
            assert not container.has_class("-focused")
            lv.focus()
            await wait_until(pilot, lambda: row.styles.background == select)
            assert container.has_class("-focused")

    asyncio.run(run())


def test_footer_rebuilds_once_and_only_on_a_change(ui_app, monkeypatch):
    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            await quiet(pilot)  # start-up changes the footer a few times, late on a slow runner
            footer = ui_app.screen.query_one(QobitFooter)
            rebuilds = []
            recompose = footer.recompose

            async def counted():
                rebuilds.append(1)
                await recompose()

            monkeypatch.setattr(footer, "recompose", counted)
            for _ in range(5):  # a burst, nothing changed
                footer.bindings_changed(ui_app.screen)
            await pilot.pause(0.3)
            assert rebuilds == []

            ui_app.action_switch_tab("tracks")  # other keys
            await pilot.pause(0.5)
            assert len(rebuilds) >= 1
            n = len(rebuilds)
            for _ in range(5):
                footer.bindings_changed(ui_app.screen)
            await pilot.pause(0.3)
            assert len(rebuilds) == n
            assert "Filter" in [k.description for k in footer.query("FooterKey")]

    asyncio.run(run())


def test_a_hidden_tab_does_not_take_the_focus(ui_app):
    """A tab's deferred focus that runs after the user switched away (the
    Playback tab's, busy mounting its rows at startup) left the focus on a
    hidden list."""

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(0.2)
            ui_app.action_switch_tab("albums")
            await pilot.pause(0.3)
            queue = ui_app.screen.query_one("#view-queue")
            queue.activate()  # late: its tab is hidden now
            await pilot.pause(0.3)
            assert ui_app.focused is not ui_app.screen.query_one("#queue-list")

    asyncio.run(run())
