"""The `i` menu's option builders: which rows appear, and their wording,
follow the favourite state of the track, album or artist."""

from __future__ import annotations

from qobit.ui.screens.context_menu import album_option, artist_option, track_options


def _ids(options):
    return [o[0] for o in options]


def test_track_options_put_favourite_after_radio():
    assert _ids(track_options(favorite=False)) == [
        "play_next",
        "add_queue",
        "radio",
        "favorite",
        "artist",
        "album",
    ]


def test_track_favourite_label_follows_state():
    labels = {o[0]: o[2] for o in track_options(favorite=True)}
    assert labels["favorite"] == "Remove from favourites"
    labels = {o[0]: o[2] for o in track_options(favorite=False)}
    assert labels["favorite"] == "Add to favourites"


def test_remove_from_queue_only_for_up_next_rows():
    assert "remove_queue" not in _ids(track_options(favorite=False))
    assert _ids(track_options(favorite=False, in_queue=True))[-1] == "remove_queue"


def test_album_and_artist_options():
    assert album_option(True) == ("favorite_album", "♥", "Remove album from favourites")
    assert album_option(False) == ("favorite_album", "♡", "Add album to favourites")
    assert artist_option(True) == ("follow_artist", "♥", "Unfollow artist")
    assert artist_option(False) == ("follow_artist", "♡", "Follow artist")


def test_menu_options_wrap_around():
    """Up on the first option goes to the last; down on the last to the first."""
    import asyncio

    from textual.app import App

    from qobit.ui.screens.context_menu import ContextMenu

    options = track_options(favorite=False)

    async def run():
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(ContextMenu("Title", "Artist", options))
            await pilot.pause()
            lv = app.screen.query_one("#menu-options")
            assert lv.index == 0
            await pilot.press("up")
            last = lv.index
            await pilot.press("down")
            return last, lv.index

    last, first = asyncio.run(run())
    assert (last, first) == (len(options) - 1, 0)


def test_page_behind_a_menu_does_not_shift():
    """Textual draws the page behind a modal inside the modal's padding; the
    app-wide `Screen { padding: 0 1 }` made it jump one column right when the
    `i`/`g` menu opened. QobitApp.CSS zeroes ModalScreen padding."""
    import asyncio
    import re

    from textual.app import App
    from textual.widgets import Label

    from qobit.ui.app import QobitApp
    from qobit.ui.screens.context_menu import ContextMenu

    class _App(App):
        CSS = QobitApp.CSS

        def compose(self):
            yield Label("MARKER")

    def marker_x(svg: str) -> str:
        return re.search(r'<text[^>]*x="([\d.]+)"[^>]*>MARKER', svg).group(1)

    async def run():
        app = _App()
        async with app.run_test(size=(40, 10)) as pilot:
            before = marker_x(app.export_screenshot())
            app.push_screen(ContextMenu("T", "A", track_options(favorite=False)))
            await pilot.pause()
            return before, marker_x(app.export_screenshot())

    before, with_menu = asyncio.run(run())
    assert before == with_menu


def test_images_behind_a_menu_keep_their_ids():
    """Kitty images are placeholder cells whose text colour encodes the image
    id. A tinted modal background changed those colours, so every image behind
    the `i`/`g` menu vanished. QobitApp.CSS keeps modal backgrounds clear."""
    import asyncio
    import re

    from PIL import Image
    from textual.app import App
    from textual_image.widget import TGPImage

    from qobit.ui.app import QobitApp
    from qobit.ui.screens.context_menu import ContextMenu

    class _App(App):
        CSS = QobitApp.CSS + " TGPImage { width: 10; height: 5; }"

        def compose(self):
            yield TGPImage(Image.new("RGB", (40, 40), "red"))

    def placeholder_colours(svg: str) -> set[str]:
        fills = dict(re.findall(r"\.(terminal-[\w-]+) \{ fill: (#[0-9a-fA-F]+)", svg))
        return {
            fills.get(cls, "?")
            for cls, text in re.findall(r'<text class="(terminal-[\w-]+)"[^>]*>([^<]*)</text>', svg)
            if "\U0010eeee" in text
        }

    async def run():
        app = _App()
        async with app.run_test(size=(40, 14)) as pilot:
            await pilot.pause()
            before = placeholder_colours(app.export_screenshot())
            app.push_screen(ContextMenu("T", "A", track_options(favorite=False)))
            await pilot.pause()
            return before, placeholder_colours(app.export_screenshot())

    before, with_menu = asyncio.run(run())
    assert before and before == with_menu
