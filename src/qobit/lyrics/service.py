"""Finding a track's lyrics: the disk cache first, then each provider in turn.

UI-agnostic. ``lookup`` never raises: offline or a provider down is a
``failed`` result (not remembered), nothing found is ``none``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from . import cache
from .model import Lyrics, Query
from .providers import LrcLib, Provider, ProviderError


@dataclass
class Result:
    status: str  # "found", "instrumental", "none" or "failed"
    lyrics: Lyrics | None = None
    offset: float = 0.0  # the reader's sync offset for this track


class LyricsService:
    def __init__(self, providers: list[Provider]) -> None:
        self._providers = providers
        self._inflight: dict[str, asyncio.Task[Result]] = {}

    @classmethod
    def default(cls) -> LyricsService:
        return cls([LrcLib()])

    async def aclose(self) -> None:
        for provider in self._providers:
            await provider.aclose()

    async def lookup(self, query: Query) -> Result:
        """Cached, else asked. A track asked for twice at once (the early
        fetch and the track starting) is looked up once."""
        k = cache.key(query)
        task = self._inflight.get(k)
        if task is None:
            task = asyncio.ensure_future(self._lookup(query))
            self._inflight[k] = task
            task.add_done_callback(lambda _: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _lookup(self, query: Query) -> Result:
        entry = cache.load(query)
        if entry is not None and entry.believed():
            return _result(entry.lyrics, entry.offset)
        offset = entry.offset if entry else 0.0
        since = cache.epoch()
        failed = False
        for provider in self._providers:
            try:
                lyrics = await provider.find(query)
            except ProviderError:
                failed = True
                continue
            if lyrics is not None:
                await asyncio.to_thread(cache.save, query, lyrics, since)
                return _result(lyrics, offset)
        if failed:
            return Result("failed", offset=offset)
        await asyncio.to_thread(cache.save, query, None, since)
        return Result("none", offset=offset)

    async def set_offset(self, query: Query, offset: float) -> None:
        await asyncio.to_thread(cache.update, query, offset=offset)

    async def reject(self, query: Query) -> None:
        await asyncio.to_thread(cache.update, query, reject=True)


def _result(lyrics: Lyrics | None, offset: float) -> Result:
    if lyrics is None:
        return Result("none", offset=offset)
    return Result("instrumental" if lyrics.instrumental else "found", lyrics, offset)
