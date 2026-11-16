"""The TUI login dialog (ui/screens/login.py) and its wiring in QobitApp:
shown at startup without a session and on a 401 (once), finished by a
pasted address, saved, the library reloaded; Esc and logout. Headless; the
fake client runs the real loopback server (qobuz/oauth.py), no Qobuz."""

from __future__ import annotations

import asyncio
import functools

import pytest
from conftest import StubClient
from textual.events import Paste
from textual.widgets import Input, Label

from qobit import config
from qobit.qobuz import oauth
from qobit.ui import app as app_module
from qobit.ui.screens.login import LoginScreen


def sync(fn):
    """Run an async test body (no async pytest plugin in this project)."""

    @functools.wraps(fn)
    def run(*args, **kwargs):
        asyncio.run(fn(*args, **kwargs))

    return run


class LoginClient(StubClient):
    user_auth_token = None

    async def login_oauth(self, show_url):
        async with oauth.redirect_server("123") as prompt:
            show_url(prompt)
            await prompt.wait(timeout=5)
        self.user_auth_token = "new-token"
        self.account = {"name": "Listener", "plan": "Studio"}

    def session(self):
        return "123", self.user_auth_token, ["s"]


@pytest.fixture
def login_app(ui_app, monkeypatch):
    monkeypatch.setattr(app_module, "QobuzClient", LoginClient)
    return app_module.QobitApp(connect=False)


def _addr(screen: LoginScreen, query="code_autorisation=abc") -> str:
    return f"http://localhost:{screen._prompt.port}{screen._prompt.path}?{query}"


async def _until(pilot, cond, tries=50):
    for _ in range(tries):
        if cond():
            return
        await pilot.pause(0.02)
    raise AssertionError("condition never met")


@sync
async def test_no_session_opens_the_dialog_with_the_link(login_app):
    async with login_app.run_test(size=(120, 40)) as pilot:
        await _until(pilot, lambda: isinstance(login_app.screen, LoginScreen))
        screen = login_app.screen
        await _until(pilot, lambda: screen._prompt is not None)
        link = str(screen.query_one("#login-link", Label).render())
        assert "https://www.qobuz.com/signin/oauth" in link.replace("\n", "")


@sync
async def test_a_pasted_address_logs_in_saves_and_reloads(login_app):
    async with login_app.run_test(size=(120, 40)) as pilot:
        await _until(pilot, lambda: isinstance(login_app.screen, LoginScreen))
        screen = login_app.screen
        await _until(pilot, lambda: screen._prompt is not None)
        tracks = login_app.screen_stack[0].query_one("#view-tracks")
        tracks._loaded = True  # pretend it loaded (and failed) before the login
        screen.post_message(Paste(_addr(screen)))
        await _until(pilot, lambda: not isinstance(login_app.screen, LoginScreen))
        app_id, token, _ = config.get_oauth_session()
        assert (app_id, token) == ("123", "new-token")
        assert config.get_account() == {"name": "Listener", "plan": "Studio"}
        assert tracks._loaded is False  # reloads on its next activation
        assert "Listener" in login_app.status_msg


@sync
async def test_a_wrong_paste_explains_and_keeps_waiting(login_app):
    async with login_app.run_test(size=(120, 40)) as pilot:
        await _until(pilot, lambda: isinstance(login_app.screen, LoginScreen))
        screen = login_app.screen
        await _until(pilot, lambda: screen._prompt is not None)
        screen.post_message(Paste("http://localhost:1/nope?code_autorisation=x"))
        await pilot.pause()
        status = screen.query_one("#login-status", Label)
        assert "different login" in str(status.render())
        assert status.has_class("-error")
        assert isinstance(login_app.screen, LoginScreen)


@sync
async def test_escape_leaves_qobit_logged_out(login_app):
    async with login_app.run_test(size=(120, 40)) as pilot:
        await _until(pilot, lambda: isinstance(login_app.screen, LoginScreen))
        await pilot.press("escape")
        await _until(pilot, lambda: not isinstance(login_app.screen, LoginScreen))
        assert "Not logged in" in login_app.status_msg
        assert config.get_oauth_session()[1] == ""
        # closed through dismiss, so a later 401 opens it again (a bare
        # pop_screen once left the app thinking it was still open)
        login_app.request_login("again")
        await _until(pilot, lambda: isinstance(login_app.screen, LoginScreen))


@sync
async def test_keys_copy_the_link_but_type_into_the_paste_field(login_app, monkeypatch):
    copied = []
    monkeypatch.setattr(login_app, "copy_to_clipboard", copied.append)
    async with login_app.run_test(size=(120, 40)) as pilot:
        await _until(pilot, lambda: isinstance(login_app.screen, LoginScreen))
        screen = login_app.screen
        await _until(pilot, lambda: screen._prompt is not None)
        await pilot.press("c")
        assert copied == [screen._prompt.url]
        field = screen.query_one("#login-paste", Input)
        field.focus()
        await pilot.press("c", "o")
        assert field.value == "co" and len(copied) == 1


@sync
async def test_a_burst_of_401s_opens_one_dialog(ui_app):
    async with ui_app.run_test(size=(120, 40)) as pilot:
        assert not isinstance(ui_app.screen, LoginScreen)  # logged in (stub)
        ui_app._client.login_oauth = LoginClient.login_oauth.__get__(ui_app._client)
        for _ in range(3):
            ui_app._client.on_auth_expired()
        await _until(pilot, lambda: isinstance(ui_app.screen, LoginScreen))
        await pilot.pause(0.1)
        assert sum(isinstance(s, LoginScreen) for s in ui_app.screen_stack) == 1
        reason = str(ui_app.screen.query_one("#login-reason", Label).render())
        assert "expired" in reason


@sync
async def test_logout_forgets_the_session_and_offers_a_login(ui_app):
    config.save_oauth_session("123", "tok", ["s"], {"name": "Listener", "plan": ""})
    async with ui_app.run_test(size=(120, 40)) as pilot:
        ui_app._client.login_oauth = LoginClient.login_oauth.__get__(ui_app._client)
        ui_app.action_logout()
        await _until(pilot, lambda: isinstance(ui_app.screen, LoginScreen))
        assert config.get_oauth_session() == ("", "", [])
        assert ui_app._client.user_auth_token is None
