"""The LRC format (plain and enhanced) and plain text.

LRC is a text file where each line starts with one or more timestamps:
``[01:23.45]the words``. What is looked after here:

- A line can carry several stamps (a refrain written once): each one is a line.
- Stamps come as ``mm:ss``, ``mm:ss.x``, ``mm:ss.xx`` and ``mm:ss.xxx``; the
  fraction is a fraction (``.5`` is 500 ms, not 5).
- Enhanced LRC times each word with ``<mm:ss.xx>``; those are timing, not text.
- A stamp with no words is kept: it clears the line (a pause, an instrumental).
- ``[offset:ms]`` shifts every stamp (positive: the lyrics come earlier).
"""

from __future__ import annotations

import re

from .model import Line, Lyrics

_STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_WORD_STAMP = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
_OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)


def _seconds(minutes: str, seconds: str, fraction: str | None) -> float:
    return int(minutes) * 60 + int(seconds) + (float(f"0.{fraction}") if fraction else 0.0)


def parse_lrc(text: str) -> Lyrics:
    """Timed lyrics from LRC text; ``synced`` is False if no line had a stamp."""
    offset = 0.0
    timed: list[Line] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if m := _OFFSET.match(raw):
            offset = int(m.group(1)) / 1000
            continue
        stamps: list[float] = []
        pos = 0
        while m := _STAMP.match(raw, pos):
            stamps.append(_seconds(*m.groups()))
            pos = m.end()
        if not stamps:
            continue  # a tag ([ar:...]) or noise
        words = _WORD_STAMP.sub("", raw[pos:]).strip()
        timed.extend(Line(words, max(0.0, t - offset)) for t in stamps)
    timed.sort(key=lambda line: line.time or 0.0)  # stable: equal times keep their order
    return Lyrics(lines=timed, synced=bool(timed))


def parse_plain(text: str) -> Lyrics:
    """Untimed lyrics: one line per line, blank lines kept once (stanzas)."""
    lines: list[Line] = []
    for raw in text.splitlines():
        raw = raw.rstrip()
        if not raw and (not lines or not lines[-1].text):
            continue
        lines.append(Line(raw))
    while lines and not lines[-1].text:
        lines.pop()
    return Lyrics(lines=lines, synced=False)
