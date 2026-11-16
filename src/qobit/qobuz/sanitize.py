"""Strip terminal control characters from text that comes from Qobuz.

Textual (and Rich) pass ESC through to the terminal, so a title, artist,
playlist name or description carrying an escape sequence would reach it
raw: OSC 52 rewrites the clipboard (Kitty allows it by default), CSI
sequences redraw the screen. Public playlists are written by other users,
so this is attacker-controlled text. Every JSON response is cleaned once, in
QobuzClient, before anything parses or shows it.

Removed: C0 controls except tab and newline (a bio has line breaks), DEL,
and C1 controls (U+0080–U+009F; some terminals read U+009B as CSI).
"""

import re
from typing import Any

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(text: str) -> str:
    return _CONTROL.sub("", text)


def clean_json(value: Any) -> Any:
    """``value`` (parsed JSON) with every string cleaned, keys included."""
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, dict):
        return {clean(k) if isinstance(k, str) else k: clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    return value
