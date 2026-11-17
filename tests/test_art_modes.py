"""Cover art modes (`_images.IMAGE_MODES`) and what each costs.

Kitty: textual-image built a new renderable on every render, so each
repaint (a grid card selected or deselected) scaled, PNG-encoded and re-sent
the image: 20 re-sends for 10 cursor moves in the Albums grid, slow on old
machines. Blocks: the fallback for terminals without the Kitty protocol.
Off: initials only, nothing downloaded."""

from __future__ import annotations

import asyncio

import pytest
import textual_image.renderable.tgp as tgp
from PIL import Image
from textual.app import App

from qobit.ui import _images
from qobit.ui.app import QobitApp
from qobit.ui.widgets import art as art_module
from qobit.ui.widgets.art import Art

COVER = Image.new("RGB", (600, 600), (200, 40, 90))


class _App(App):
    get_theme_variable_defaults = QobitApp.get_theme_variable_defaults
    CSS = "Art { width: 10; height: 5; }"

    def compose(self):
        yield Art("Kind of Blue")


@pytest.fixture
def kitty_messages(monkeypatch):
    """What would go to the terminal: "t" for a whole image sent, "d" deleted."""
    sent: list[str] = []

    def send(**kw):
        if kw.get("a") == "d":
            sent.append("d")
        elif kw.get("a") != "p" and not kw.get("m"):  # the last chunk of an image
            sent.append("t")

    monkeypatch.setattr(tgp, "_send_tgp_message", send)
    return sent


def _rows(app) -> list[str]:
    return [s.text for s in app.screen._compositor.render_strips()[:5]]


def test_kitty_image_is_sent_once_across_repaints(kitty_messages):
    _images.set_image_mode("kitty")

    async def run():
        app = _App()
        async with app.run_test(size=(20, 6)) as pilot:
            art = app.query_one(Art)
            art.image = COVER
            await pilot.pause()
            assert kitty_messages == ["t"]
            for i in range(5):  # what selecting and deselecting a card does
                art.set_class(i % 2 == 0, "-selected")
                art.refresh()
                await pilot.pause()
            assert kitty_messages == ["t"]

            art.styles.width = 8  # a new size: a new image, the old one freed
            await pilot.pause()
            assert kitty_messages == ["t", "d", "t"]

            await art.remove()  # gone from the page: freed in the terminal too
            assert kitty_messages == ["t", "d", "t", "d"]

    asyncio.run(run())


def test_blocks_draw_half_blocks_worked_out_once(monkeypatch):
    _images.set_image_mode("blocks")
    calls = []
    inner = art_module.HalfcellRenderable.__rich_console__

    def counted(self, console, options):
        calls.append(1)
        return inner(self, console, options)

    monkeypatch.setattr(art_module.HalfcellRenderable, "__rich_console__", counted)

    async def run():
        app = _App()
        async with app.run_test(size=(20, 6)) as pilot:
            art = app.query_one(Art)
            art.image = COVER
            await pilot.pause()
            assert all(row.startswith("▀" * 10) for row in _rows(app))
            for _ in range(4):
                art.refresh()
                await pilot.pause()
            assert len(calls) == 1

    asyncio.run(run())


def test_off_shows_initials_even_with_an_image():
    _images.set_image_mode("off")

    async def run():
        app = _App()
        async with app.run_test(size=(20, 6)) as pilot:
            app.query_one(Art).image = COVER
            await pilot.pause()
            rows = _rows(app)
            assert "KO" in rows[2] and "▀" not in "".join(rows)

    asyncio.run(run())


def test_off_downloads_nothing_for_the_terminal(monkeypatch):
    _images.set_image_mode("off")

    def no_network():
        raise AssertionError("fetched with images off")

    monkeypatch.setattr(_images, "_get_client", no_network)
    assert asyncio.run(_images.fetch_image("https://example.invalid/a.jpg")) is None
    # The OS media widget's artwork still loads (here: from the memory cache).
    _images._cache_put("https://example.invalid/b.jpg", COVER)
    got = asyncio.run(_images.fetch_image("https://example.invalid/b.jpg", terminal=False))
    assert got is COVER


@pytest.mark.parametrize("setting", ["auto", "nonsense"])
def test_auto_resolves_to_what_the_terminal_supports(setting):
    assert _images.set_image_mode(setting) == _images.detected_image_mode()
    assert _images.image_mode() in ("kitty", "blocks")


def test_palette_switch_applies_and_saves(ui_app):
    from qobit import config

    async def run():
        async with ui_app.run_test(size=(130, 40)) as pilot:
            labels = [label for label, _cb, _help in ui_app.image_mode_commands()]
            assert "Cover art: auto" not in labels  # the current setting
            assert "Cover art: off" in labels
            ui_app.set_image_setting("off")
            await pilot.pause()
            assert _images.image_mode() == "off"
            assert config.get_image_mode() == "off"
            assert "Cover art: off" in ui_app.status_msg

    asyncio.run(run())


def test_cli_images_flag_overrides_for_the_run(ui_app):
    from qobit import config

    QobitApp(connect=False, images="blocks")
    assert _images.image_mode() == "blocks"
    assert config.get_image_mode() == "auto"  # not saved
