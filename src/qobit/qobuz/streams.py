"""Finding a stream URL for a track: the quality fallback and what it means.

UI-agnostic: it needs a Qobuz client and nothing else. The app asks it when a
track starts and when the next one is lined up (gapless).
"""

from __future__ import annotations

from .client import AuthExpiredError, QobuzClient, QobuzError
from .models import NoStreamError, StreamUrl

# Best first. A track's stream is asked at each in turn.
QUALITY_FALLBACK = ["FLAC_24_192", "FLAC_24_96", "FLAC_CD"]
_CACHE_MAX = 1000


class StreamResolver:
    def __init__(self, client: QobuzClient) -> None:
        self.client = client
        # track id -> the quality that worked last time (asked first next time)
        self._worked: dict[str, str] = {}

    async def resolve(self, track_id: str) -> StreamUrl | None:
        """Walk the quality fallback chain and return the first stream Qobuz
        hands back, or None. A preview (``stream.sample``) is returned as-is
        and ends the walk: Qobuz returns the same preview at every quality, so
        trying the rest would only cost round-trips. Raises AuthExpiredError,
        and NoStreamError when every quality was refused by a restriction (no
        URL, no preview): unavailable, unlike a network failure (None)."""
        # Prefer the quality that worked last time for this track; fall through to
        # the full list on first play or if the cached quality fails.
        cached = self._worked.get(track_id)
        if cached:
            qualities = [cached] + [q for q in QUALITY_FALLBACK if q != cached]
        else:
            qualities = QUALITY_FALLBACK
        restricted: NoStreamError | None = None
        failed = False
        for quality in qualities:
            try:
                data = await self.client.get_streaming_url(track_id, quality)
            except AuthExpiredError:
                raise
            except QobuzError:
                failed = True
                continue
            try:
                stream = StreamUrl.from_api(data)
            except NoStreamError as e:
                restricted = e
                continue  # restricted at this quality; maybe not at the next
            if stream.sample:
                return stream
            self._worked[track_id] = quality
            if len(self._worked) > _CACHE_MAX:
                del self._worked[next(iter(self._worked))]
            return stream
        if restricted is not None and not failed:
            raise restricted
        return None
