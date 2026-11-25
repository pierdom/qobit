"""Reading mpv on each poll tick, and what the reading means.

The one rule that matters: how a track *ended*. mpv no longer running with the
same stop generation as the last time it ran is a natural end (the player
advances); a different generation means ``MpvPlayer.stop()`` was called, which
is never an end. ``MpvPlayer.snapshot()`` reads the generation and the running
flag together so a stop racing the poll cannot look like a natural end.

No Textual here: the app's poll thread calls ``read()`` and applies the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .player import EntryWatch, MpvPlayer


@dataclass(frozen=True)
class Reading:
    # "playing": mpv is running. "stopped": it was playing and no longer is.
    # "idle": it isn't running and wasn't playing.
    state: Literal["playing", "stopped", "idle"]
    moved_on: bool = False  # mpv went on to the entry queued behind the last one
    pos: float | None = None  # seconds; None when mpv didn't answer
    dur: float | None = None
    paused: bool | None = None
    natural_end: bool = False  # "stopped" only: the track ended on its own


class PlayerWatch:
    def __init__(self, player: MpvPlayer) -> None:
        self._player = player
        self._seen_gen = 0  # the stop generation when mpv last ran
        self._entries = EntryWatch()

    def read(self, was_playing: bool) -> Reading:
        """One tick. ``was_playing``: the app showed a track as playing."""
        gen, running = self._player.snapshot()
        if running:
            self._seen_gen = gen
            moved_on = self._entries.moved_on(gen, self._player.get_property("playlist-pos"))
            return Reading(
                "playing",
                moved_on=moved_on,
                pos=_number(self._player.get_property("time-pos")),
                dur=_number(self._player.get_property("duration")),
                paused=_flag(self._player.get_property("pause")),
            )
        if was_playing:
            self._entries.reset()
            return Reading("stopped", natural_end=gen == self._seen_gen)
        return Reading("idle")


def _number(value: object) -> float | None:
    return None if value is None else float(cast(float, value))


def _flag(value: object) -> bool | None:
    return None if value is None else bool(value)
