from __future__ import annotations

import asyncio
import io

import httpx
from PIL import Image as PILImage

# Cap concurrent HTTP fetches so a full library grid doesn't flood the
# network or thrash PIL/Kitty rendering all at once.
_FETCH_SEM = asyncio.Semaphore(6)

# URL → decoded PIL Image.  Shared across all cards and headers so the same
# artist/album art is never downloaded twice within a session.
_IMAGE_CACHE: dict[str, PILImage.Image] = {}


async def fetch_image(url: str) -> PILImage.Image | None:
    if url in _IMAGE_CACHE:
        return _IMAGE_CACHE[url]
    async with _FETCH_SEM:
        # Re-check after acquiring: another worker may have fetched it while
        # we were waiting.
        if url in _IMAGE_CACHE:
            return _IMAGE_CACHE[url]
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                r = await http.get(url)
                r.raise_for_status()
            img = PILImage.open(io.BytesIO(r.content))
            _IMAGE_CACHE[url] = img
            return img
        except Exception:
            return None
