"""Artist tiles render (a helper named `_name` once shadowed Textual's own
Widget._name and crashed every artist tile on compose) and show ♥ for
followed artists."""

from __future__ import annotations

import asyncio

from textual.app import App
from textual.widgets import Label

from qobit.qobuz.models import Artist
from qobit.ui.screens.artist_detail import ArtistCard


class _App(App):
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
