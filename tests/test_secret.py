"""Credentials never show in a crash screen: Textual prints every frame's
locals (Rich traceback, show_locals=True), and crash screens get pasted into
bug reports. Secret masks its repr and works as the plain string."""

from __future__ import annotations

import asyncio
import json

import httpx
from rich.console import Console
from rich.traceback import Traceback

from qobit import config
from qobit.qobuz.client import QobuzClient
from qobit.secret import Secret

TOKEN = "tok-0123456789abcdef"


def test_secret_masks_repr_but_is_the_string():
    s = Secret(TOKEN)
    assert repr(s) == "'<redacted>'" and repr({"h": s}) == "{'h': '<redacted>'}"
    assert s == TOKEN and f"{s}" == TOKEN and json.dumps(s) == f'"{TOKEN}"'


def test_saved_and_restored_tokens_are_secrets():
    config.save_oauth_session("123", TOKEN, ["s"])
    _, token, _ = config.get_oauth_session()
    assert isinstance(token, Secret)
    client = QobuzClient()
    client.restore_session("123", token, ["s"])
    assert isinstance(client.user_auth_token, Secret)


def test_a_crash_during_an_api_call_doesnt_print_the_token():
    def handler(request):
        raise RuntimeError("boom inside a request")

    client = QobuzClient()
    client.restore_session("123", str(TOKEN), ["s"])  # a plain str goes in
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    console = Console(record=True, width=200)
    try:
        asyncio.run(client.search("x"))
    except Exception:
        console.print(Traceback(show_locals=True))
    text = console.export_text()
    assert "locals" in text and "<redacted>" in text
    assert TOKEN not in text
