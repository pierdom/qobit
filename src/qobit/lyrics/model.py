"""One shape for a track's lyrics, whoever supplied them."""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from functools import cached_property


@dataclass(frozen=True)
class Query:
    """What is known about the track lyrics are wanted for."""

    title: str
    artist: str
    album: str = ""
    duration: int = 0  # seconds; 0 = unknown
    isrc: str | None = None
    version: str = ""  # "Live", "Remastered 2011"...: Qobuz keeps it apart from the title


@dataclass(frozen=True)
class Line:
    text: str
    time: float | None = None  # seconds; None when the lyrics aren't timed


@dataclass
class Lyrics:
    lines: list[Line] = field(default_factory=list)
    synced: bool = False  # every line has its time
    instrumental: bool = False  # a track with nothing sung
    source: str = ""  # who supplied it ("LRCLIB")

    @cached_property
    def _times(self) -> list[float]:
        return [line.time for line in self.lines if line.time is not None]

    def line_at(self, position: float) -> int:
        """Index of the line being sung at ``position`` seconds, -1 before the
        first one. Only meaningful for synced lyrics."""
        return bisect.bisect_right(self._times, position) - 1

    def to_json(self) -> dict:
        return {
            "lines": [[line.text, line.time] for line in self.lines],
            "synced": self.synced,
            "instrumental": self.instrumental,
            "source": self.source,
        }

    @classmethod
    def from_json(cls, data: dict) -> Lyrics:
        lines = [Line(str(text), None if t is None else float(t)) for text, t in data["lines"]]
        return cls(
            lines=lines,
            synced=bool(data.get("synced")),
            instrumental=bool(data.get("instrumental")),
            source=str(data.get("source") or ""),
        )
