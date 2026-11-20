"""Who is asked for lyrics. Each provider answers a ``Query`` with lyrics, None
(it looked, there are none) or raises ``ProviderError`` (it could not look:
offline, a server error; never remembered, asked again next time).

LRCLIB (https://lrclib.net) is open, needs no key and has timed and plain
lyrics. It is asked for the exact track first, then searched by title and
artist; either way the answer is judged against the track
(``matching.pick``) before it is used. The service asks every provider in
order, so another one is an entry in the list.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from .. import __version__
from ..qobuz.sanitize import clean_json
from .matching import Candidate, pick
from .model import Lyrics, Query

TIMEOUT = 6.0


class ProviderError(Exception):
    """The provider could not be asked or did not answer."""


class Provider(Protocol):
    name: str

    async def find(self, query: Query) -> Lyrics | None: ...

    async def aclose(self) -> None: ...


def _candidate(d: dict) -> Candidate | None:
    if not isinstance(d, dict):
        return None
    duration = d.get("duration")
    return Candidate(
        title=str(d.get("trackName") or ""),
        artist=str(d.get("artistName") or ""),
        album=str(d.get("albumName") or ""),
        duration=float(duration) if isinstance(duration, (int, float)) else None,
        synced=d.get("syncedLyrics") or None,
        plain=d.get("plainLyrics") or None,
        instrumental=bool(d.get("instrumental")),
    )


class LrcLib:
    name = "LRCLIB"
    BASE = "https://lrclib.net"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.BASE,
                timeout=TIMEOUT,
                headers={"User-Agent": f"qobit/{__version__} (https://github.com/pierdom/qobit)"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def find(self, query: Query) -> Lyrics | None:
        found = None
        if query.album and query.duration:  # the exact lookup needs both
            data = await self._get(
                "/api/get",
                track_name=query.title,
                artist_name=query.artist,
                album_name=query.album,
                duration=query.duration,
            )
            found = pick(query, [c for c in [_candidate(data)] if c]) if data else None
        if found is None:
            data = await self._get("/api/search", track_name=query.title, artist_name=query.artist)
            rows = data if isinstance(data, list) else []
            found = pick(query, [c for row in rows if (c := _candidate(row))])
        if found is not None:
            found.source = self.name
        return found

    async def _get(self, path: str, **params: Any) -> Any:
        """The decoded answer; None when the service says it has no such track."""
        try:
            r = await self._http().get(path, params=params)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name}: {e or type(e).__name__}") from e
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ProviderError(f"{self.name}: HTTP {r.status_code}")
        try:
            return clean_json(r.json())
        except ValueError as e:
            raise ProviderError(f"{self.name}: unreadable answer") from e
