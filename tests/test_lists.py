"""PagedListView's lazy paging: long lists mount a page at a time, but `rows`
and End always cover the whole list (the Tracks tab used to stop at 200)."""

from __future__ import annotations

import asyncio

from textual.app import App
from textual.widgets import Label, ListItem

from qobit.ui.widgets.lists import _PAGE_ROWS, PagedListView


class _App(App):
    def compose(self):
        yield PagedListView(id="lv")


def _rows(n: int) -> list[ListItem]:
    return [ListItem(Label(str(i))) for i in range(n)]


def test_set_rows_mounts_a_page_and_end_mounts_the_rest():
    async def run():
        app = _App()
        async with app.run_test() as pilot:
            lv = app.query_one(PagedListView)
            rows = _rows(_PAGE_ROWS * 2 + 50)
            await lv.set_rows(rows)
            assert len(lv.children) == _PAGE_ROWS
            assert lv.rows == rows  # order kept, unmounted rows included
            lv.focus()
            await pilot.press("end")
            await pilot.pause()
            assert len(lv.children) == len(rows)
            assert lv.index == len(rows) - 1

    asyncio.run(run())


def test_cursor_near_the_end_mounts_the_next_page():
    async def run():
        app = _App()
        async with app.run_test() as pilot:
            lv = app.query_one(PagedListView)
            await lv.set_rows(_rows(_PAGE_ROWS * 3))
            lv.index = _PAGE_ROWS - 10
            await pilot.pause()
            assert len(lv.children) == _PAGE_ROWS * 2

    asyncio.run(run())


def test_remove_row_works_for_unmounted_rows():
    async def run():
        app = _App()
        async with app.run_test():
            lv = app.query_one(PagedListView)
            rows = _rows(_PAGE_ROWS + 5)
            await lv.set_rows(rows)
            lv.remove_row(rows[-1])
            assert len(lv.rows) == _PAGE_ROWS + 4
            assert rows[-1] not in lv.rows

    asyncio.run(run())
