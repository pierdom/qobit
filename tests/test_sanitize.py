"""Text from Qobuz can't drive the terminal (qobuz/sanitize.py): verified
live that Textual passes ESC through, so OSC 52 in a playlist name would
rewrite the clipboard."""

from __future__ import annotations

import httpx

from qobit.qobuz.client import QobuzClient, _json
from qobit.qobuz.sanitize import clean, clean_json

EVIL = "Evil\x1b]52;c;ZXZpbA==\x07Name\x1b[2J\x9b31m\r"


def test_clean_strips_escape_sequences_starters_and_keeps_text():
    assert clean(EVIL) == "Evil]52;c;ZXZpbA==Name[2J31m"
    assert "\x1b" not in clean(EVIL) and "\x9b" not in clean(EVIL)


def test_clean_keeps_newlines_tabs_and_unicode():
    text = "Björk\tSigur Rós\nÓlafur Arnalds — 坂本龍一 ♥"
    assert clean(text) == text


def test_clean_json_reaches_every_string():
    data = {"a": [EVIL, {"b": EVIL, EVIL: 1}], "n": 3, "t": True, "x": None}
    out = clean_json(data)
    assert "\x1b" not in repr(out)
    assert out["n"] == 3 and out["t"] is True and out["x"] is None


def test_every_client_response_is_cleaned():
    r = httpx.Response(200, json={"playlists": {"items": [{"name": EVIL}]}})
    assert "\x1b" not in repr(_json(r))


def test_error_text_is_cleaned_and_bounded():
    r = httpx.Response(500, text="\x1b[2J" + "x" * 5000)
    try:
        QobuzClient._raise_for_status(r)
    except Exception as e:
        msg = str(e)
    assert "\x1b" not in msg and len(msg) < 400
