"""Artist tiles render (a helper named `_name` once shadowed Textual's own
Widget._name and crashed every artist tile on compose) and show ♥ for
followed artists."""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App
from textual.widgets import Label

from qobit.qobuz.models import Artist
from qobit.ui.app import QobitApp
from qobit.ui.screens.artist_detail import ArtistCard


class _App(App):
    # qobit's widgets use qobit's theme variables ($qobit-*).
    get_theme_variable_defaults = QobitApp.get_theme_variable_defaults

    def __init__(self, card: ArtistCard) -> None:
        super().__init__()
        self._card = card

    def compose(self):
        yield self._card


def _label_text(followed: bool) -> str:
    async def run() -> str:
        card = ArtistCard(Artist(id="1", name="Radiohead"), followed=followed)
        app = _App(card)
        async with app.run_test():
            return str(card.query_one(".card-name", Label).render())

    return asyncio.run(run())


def test_artist_card_renders_name():
    assert _label_text(False).strip() == "Radiohead"


def test_followed_artist_card_shows_heart():
    assert "♥" in _label_text(True)


def test_clicked_label_maps_title_clicks_to_views():
    """A click on a name in a two-view panel title picks that view. The title
    starts 3 cells in (corner, rule, space); names are joined by `  │  `."""
    from types import SimpleNamespace

    from textual.geometry import Region

    from qobit.ui.screens.artist_detail import clicked_label, switch_title

    labels = ["Albums & EPs", "Similar artists  ·  79"]
    panel = SimpleNamespace(region=Region(10, 5, 80, 20))

    def click(col: int, row: int = 5):
        return clicked_label(panel, SimpleNamespace(screen_x=10 + col, screen_y=row), labels)

    assert click(3) == 0  # first letter of "Albums & EPs"
    assert click(3 + len(labels[0]) + 5) == 1  # first letter after the separator
    assert click(3 + len(labels[0]) + 1) is None  # on the separator
    assert click(3, row=6) is None  # not the title row
    assert switch_title(labels, 1) == (
        "[not bold]Albums & EPs[/not bold]  │  [underline]Similar artists  ·  79[/underline]"
    )


@pytest.mark.parametrize("width", [80, 130])  # one line; HI-RES below the year
def test_narrow_album_card_keeps_hires(ui_app, width):
    """An artist page's small tiles had room for "1959  ·" but not HI-RES:
    the line wrapped after the dot and the one-row label cut the rest."""
    from album_pages import ALBUM, ARTIST, stub_album_client, wait_until

    from qobit.ui.screens.artist_detail import AlbumCard, ArtistScreen

    stub_album_client(ui_app)
    albums = [{**ALBUM, "id": f"a{i}", "hires_streamable": True} for i in range(4)]

    async def artist(*_a, **_k):
        return {**ARTIST, "albums": {"items": albums}}

    ui_app._client.get_artist_detail = artist

    def card_lines() -> list[str]:
        card = ui_app.screen.query(AlbumCard).first()
        r = card.region
        strips = ui_app.screen._compositor.render_strips()[r.y : r.y + r.height]
        return [s.text[r.x : r.right].rstrip() for s in strips]

    def settled() -> bool:
        lines = card_lines()
        return "HI-RES" in "\n".join(lines) and not any(ln.endswith("·") for ln in lines)

    async def run() -> None:
        async with ui_app.run_test(size=(width, 50)) as pilot:
            await ui_app.push_screen(ArtistScreen("r1"))
            await wait_until(pilot, lambda: len(ui_app.screen.query(AlbumCard)) == 4)
            await wait_until(pilot, settled)
            await pilot.pause(0.2)  # and it stays that way
            assert settled(), card_lines()

    asyncio.run(run())
