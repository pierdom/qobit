"""LoginScreen: log in to Qobuz without leaving the TUI.

Opened by QobitApp at startup when there's no saved session, when Qobuz
answers 401 (the session expired or was revoked), and from the command
palette. It runs ``QobuzClient.login_oauth`` and shows its ``LoginPrompt``:
the link (always shown, copyable, clickable; the browser also opens when
there's a display) and a field for the address the browser ends on when the
login happened on another device. A paste anywhere in the dialog goes there.
Dismisses True once logged in (the caller saves the session), False on Esc.
"""

from __future__ import annotations

import contextlib

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.events import Paste
from textual.screen import ModalScreen
from textual.style import Style
from textual.widgets import Input, Label

from ...qobuz.client import QobuzClient, QobuzError
from ...qobuz.oauth import LoginPrompt
from .._utils import readout

_PASTE_REPLIES = {
    "no_code": "That address has no login code: copy the whole address from the "
    "browser's address bar, after logging in.",
    "wrong_login": "That address belongs to a different login: use the link above.",
}


def _keys(*pairs: tuple[str, str]) -> Content:
    """Key hints in the footer's style: key in the readout colour, then what
    it does."""
    parts: list[Content | tuple[str, str] | str] = []
    for i, (key, text) in enumerate(pairs):
        if i:
            parts.append("   ")
        parts += [(key, "$qobit-readout bold"), f" {text}"]
    return Content.assemble(*parts)


class _Dialog(Vertical, can_focus=True):
    """The dialog body; holds focus so single-key bindings work, while the
    paste field takes focus only when clicked or tabbed to."""


class LoginScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("c", "copy_link", "Copy link", show=False),
        Binding("o", "open_link", "Open link", show=False),
        Binding("r", "retry", "Try again", show=False),
    ]

    DEFAULT_CSS = """
    LoginScreen {
        align: center middle;
        /* No tint behind: see ModalScreen in QobitApp.CSS (Kitty images). */
    }
    LoginScreen #login {
        width: 78;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        border: round $accent;
        border-title-color: $qobit-accent-text;
        border-title-style: bold;
        background: $surface;
        padding: 1 2;
    }
    LoginScreen Label { width: 1fr; }
    LoginScreen #login-reason { color: $foreground; text-style: bold; margin-bottom: 1; }
    LoginScreen #login-link { color: $qobit-accent-text; margin: 1 0 0 0; }
    LoginScreen #login-keys { margin: 1 0 0 0; }
    LoginScreen .login-muted { color: $qobit-muted; }
    LoginScreen #login-paste-hint { margin: 2 0 0 0; }
    LoginScreen Input {
        margin: 1 0 0 0;
        border: round $accent 40%;
        padding: 0 1;
    }
    LoginScreen Input:focus { border: round $accent; }
    LoginScreen #login-status { margin: 1 0 0 0; color: $qobit-muted; }
    LoginScreen #login-status.-error { color: $error; }
    LoginScreen #login-footer { margin: 1 0 0 0; }
    """

    def __init__(self, client: QobuzClient, reason: str | None = None) -> None:
        super().__init__()
        self._client = client
        self._reason = reason
        self._prompt: LoginPrompt | None = None
        self._failed = False

    def compose(self) -> ComposeResult:
        with _Dialog(id="login") as dialog:
            dialog.border_title = readout(" Log in to Qobuz ")
            if self._reason:
                yield Label(self._reason, id="login-reason", markup=False)
            yield Label("Open this link in a browser and log in:", markup=False)
            yield Label("Preparing the login link…", id="login-link", classes="login-muted")
            yield Label(id="login-keys")
            yield Label(
                "On another device? After logging in, its browser ends on a page "
                "that doesn't load. Paste that page's address here:",
                id="login-paste-hint",
                markup=False,
            )
            yield Input(placeholder="http://localhost:…", id="login-paste")
            yield Label("", id="login-status", markup=False)
            yield Label(id="login-footer")

    def on_mount(self) -> None:
        self._set_footer()
        self.query_one("#login", _Dialog).focus()
        self._login()

    # ── the login ────────────────────────────────────────────────────────────

    @work(exclusive=True, group="_login")
    async def _login(self) -> None:
        self._failed = False
        self._set_status("Preparing the login link…")
        self._set_footer()
        try:
            await self._client.login_oauth(show_url=self._show_prompt)
        except QobuzError as e:
            if self.is_attached:
                self._failed = True
                self._prompt = None
                self._set_status(f"Login failed: {e}", error=True)
                self._set_footer()
            return
        if self.is_attached:
            self.dismiss(True)

    def _show_prompt(self, prompt: LoginPrompt) -> None:
        self._prompt = prompt
        with contextlib.suppress(NoMatches):
            link = self.query_one("#login-link", Label)
            link.remove_class("login-muted")
            link.update(Content(prompt.url).stylize(Style(link=prompt.url)))
            self.query_one("#login-keys", Label).update(
                _keys(("c", "copy link"), ("o", "open in browser"))
            )
        self._set_status("Waiting for the login…")

    def _submit(self, text: str) -> None:
        if self._prompt is None or not text.strip():
            return
        outcome = self._prompt.submit(text)
        if outcome == "ok":
            self._set_status("Logging in…")
        elif msg := _PASTE_REPLIES.get(outcome):
            self._set_status(msg, error=True)

    # ── widgets ──────────────────────────────────────────────────────────────

    def _set_status(self, text: str, *, error: bool = False) -> None:
        with contextlib.suppress(NoMatches):
            status = self.query_one("#login-status", Label)
            status.update(text)
            status.set_class(error, "-error")

    def _set_footer(self) -> None:
        keys = [("r", "try again")] if self._failed else []
        with contextlib.suppress(NoMatches):
            self.query_one("#login-footer", Label).update(
                _keys(*keys, ("esc", "later (^p → Log in)"))
            )

    # ── input ────────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit(event.value)

    def on_paste(self, event: Paste) -> None:
        """A paste while the dialog (not the field) has focus: put it in the
        field and submit it, so pasting just works."""
        field = self.query_one("#login-paste", Input)
        if field.has_focus:
            return
        event.stop()
        field.value = event.text.strip()
        self._submit(event.text)

    def action_copy_link(self) -> None:
        if self._prompt is not None:
            self.app.copy_to_clipboard(self._prompt.url)
            self._set_status("Link copied to the clipboard. Waiting for the login…")

    def action_open_link(self) -> None:
        if self._prompt is not None:
            self.app.open_url(self._prompt.url)

    def action_retry(self) -> None:
        if self._failed:
            self._login()

    def action_navigate_back(self) -> bool:
        """Esc reaches QobitApp's priority `back` binding first, which asks
        the screen before popping it: close through dismiss, so the app's
        callback runs."""
        self.dismiss(False)
        return True
