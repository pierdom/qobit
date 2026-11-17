"""The saved theme is applied before the first widget mounts. Set in
on_mount, it restyled every widget of every tab: a quarter of the startup's
CPU time, seconds on a Raspberry Pi."""

from __future__ import annotations

import asyncio

from qobit import config
from qobit.ui import app as app_module


def test_the_saved_theme_is_set_before_mounting(monkeypatch):
    config.set_saved_theme("ayu-mirage")
    from conftest import StubClient, _NoOpMediaKeys

    monkeypatch.setattr(app_module, "MediaKeys", _NoOpMediaKeys)
    monkeypatch.setattr(app_module, "QobuzClient", StubClient)
    monkeypatch.setattr(app_module, "set_saved_theme", lambda *_: None)
    app = app_module.QobitApp(connect=False)
    assert app.theme == "ayu-mirage"  # before run(): no restyle of mounted widgets


def test_first_launch_gets_cyberdeck(ui_app):
    assert ui_app.theme == app_module.CYBERDECK_THEME.name

    async def run():
        async with ui_app.run_test(size=(120, 40)):
            assert ui_app.theme == app_module.CYBERDECK_THEME.name

    asyncio.run(run())
