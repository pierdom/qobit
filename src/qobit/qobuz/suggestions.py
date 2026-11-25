"""Song radio and the DailyQ/WeeklyQ mixes: recommendations from
``dynamic/suggest`` seeded with what the listener played.

UI-agnostic: it needs a Qobuz client and the caller's history and favourites.
Both entry points are best-effort: a failed request is an empty list, and the
caller leaves the queue as it was.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .client import QobuzClient, QobuzError
from .models import Album, Track

RADIO_LISTENED = 10  # recent tracks sent as "already listened" for a radio
MIX_SEEDS = 50  # the most seeds a mix draws from history and favourites
MIX_ANALYSED = 12  # how many of them are sent with artist/label/genre ids


def as_int(value: Any) -> int | None:
    """``value`` as an int, or None (ids come as numbers or numeric strings)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _new_tracks(resp: dict, exclude: set[str]) -> list[Track]:
    """The tracks of a ``dynamic/suggest`` answer, minus ``exclude`` and
    repeats; an item that doesn't parse is skipped."""
    seen = set(exclude)
    out: list[Track] = []
    for item in (resp.get("tracks") or {}).get("items") or []:
        try:
            track = Track.from_api(item)
        except Exception:
            continue
        if str(track.id) in seen:
            continue
        seen.add(str(track.id))
        out.append(track)
    return out


class SuggestionService:
    def __init__(self, client: QobuzClient) -> None:
        self.client = client

    async def radio(
        self, seed: Track, history: list[Track], seed_album: Album | None = None
    ) -> list[Track]:
        """Suggestions seeded from ``seed`` and the recent ``history`` (oldest
        first), with those tracks left out. ``seed_album``: the seed's album if
        already fetched, which saves a ``track/get`` for its artist/label/genre."""
        listened: list[int] = []
        for track in [seed, *reversed(history)]:
            tid = as_int(track.id)
            if tid is not None and tid not in listened:
                listened.append(tid)
            if len(listened) >= RADIO_LISTENED:
                break

        entry = await self._analysed_entry(seed, seed_album)
        try:
            resp = await self.client.get_dynamic_suggestions(listened, [entry] if entry else [])
        except QobuzError:
            return []
        return _new_tracks(resp, {str(i) for i in listened})

    async def mix(self, history: list[Track], favourites: list[Track]) -> list[Track]:
        """A mix from ``history`` (oldest first) and ``favourites``. DailyQ and
        WeeklyQ use the same recipe; the Mixes tab caches them for a day and a
        week.

        Seeds up to 50 tracks, most recent history first, topped up with
        favourites. A spread of up to 12 of them is enriched with
        artist/label/genre ids for the ``track_to_analysed`` payload. An empty
        list when there are no seeds or the request fails."""
        seen: set[str] = set()
        seeds: list[Track] = []
        for t in [*reversed(history), *favourites]:
            if t.id not in seen:
                seen.add(t.id)
                seeds.append(t)
            if len(seeds) >= MIX_SEEDS:
                break
        if not seeds:
            return []

        listened = [tid for t in seeds if (tid := as_int(t.id)) is not None]

        count = min(MIX_ANALYSED, len(seeds))
        if count > 1:
            step = (len(seeds) - 1) / (count - 1)
            picks = [seeds[round(i * step)] for i in range(count)]
        else:
            picks = seeds[:count]

        results = await asyncio.gather(
            *[self._analysed_entry(t) for t in picks], return_exceptions=True
        )
        analysed = [e for e in results if isinstance(e, dict)]

        try:
            resp = await self.client.get_dynamic_suggestions(listened, analysed)
        except QobuzError:
            return []
        return _new_tracks(resp, {str(i) for i in listened})

    async def _analysed_entry(self, seed: Track, album: Album | None = None) -> dict | None:
        """One ``track_to_analysed`` entry (artist/label/genre ids) for ``seed``:
        from ``album`` when given, else from a single ``track/get``. None when
        the ids can't be had."""
        artist_id: Any
        label_id: Any
        genre_id: Any
        if album is not None:
            artist_id, label_id, genre_id = album.artist_id, album.label_id, album.genre_id
        else:
            try:
                data = await self.client.get_track(str(seed.id))
            except QobuzError:
                return None
            alb = data.get("album") or {}
            artist = alb.get("artist") or data.get("performer") or {}
            artist_id = artist.get("id")
            label_id = (alb.get("label") or {}).get("id")
            genre_id = (alb.get("genre") or {}).get("id")
        artist_id, label_id, genre_id = as_int(artist_id), as_int(label_id), as_int(genre_id)
        if artist_id is None and genre_id is None:
            return None
        return {
            "track_id": as_int(seed.id),
            "artist_id": artist_id,
            "label_id": label_id,
            "genre_id": genre_id,
        }
