"""Lyrics for the track that is playing: the LRC format, judging whether lyrics
belong to a track, the disk cache and the providers. UI-agnostic."""

from .model import Line, Lyrics, Query
from .service import LyricsService, Result

__all__ = ["Line", "Lyrics", "LyricsService", "Query", "Result"]
