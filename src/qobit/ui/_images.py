from __future__ import annotations

import asyncio
import hashlib
import io
import os
from pathlib import Path

import httpx
from PIL import Image as PILImage

# Cap concurrent HTTP fetches so a full library grid doesn't flood the
# network or thrash PIL/Kitty rendering all at once.
_FETCH_SEM = asyncio.Semaphore(6)

# URL → decoded PIL Image.  Shared across all cards and headers so the same
# artist/album art is never downloaded twice within a session.
_IMAGE_CACHE: dict[str, PILImage.Image] = {}

# The largest place we ever show art is the album detail panel (~16 cells ≈
# ~320px).  Artist images arrive as "mega" (~1500px) and album art as "large"
# (~600px); anything above this is decoded, resized and re-encoded for nothing
# on every repaint.  Capping the cached copy bounds both memory and the
# per-render resize cost textual-image pays from the source image.
_MAX_EDGE = 600

# One shared client so the ~50 image fetches that fire when a grid opens reuse
# connections / TLS sessions instead of each spinning up a fresh pool.
_client: httpx.AsyncClient | None = None

# On-disk cache of normalised covers so relaunching doesn't re-download the
# whole library's art.  Resolved lazily; ``None`` means unavailable (e.g.
# read-only fs) and the disk layer is skipped silently.
_UNSET = object()
_CACHE_DIR: Path | None | object = _UNSET


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
        )
    return _client


async def close_client() -> None:
    """Close the shared image HTTP client (call on app shutdown)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _cache_dir() -> Path | None:
    global _CACHE_DIR
    if _CACHE_DIR is _UNSET:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        d = Path(base) / "qobit" / "images"
        try:
            d.mkdir(parents=True, exist_ok=True)
            _CACHE_DIR = d
        except Exception:
            _CACHE_DIR = None
    return _CACHE_DIR  # type: ignore[return-value]


def _disk_path(url: str) -> Path | None:
    d = _cache_dir()
    if d is None:
        return None
    return d / f"{hashlib.sha1(url.encode()).hexdigest()}.jpg"


def _normalize(img: PILImage.Image) -> PILImage.Image:
    """Decode, downscale and colour-normalise once at fetch time.

    textual-image resizes the *source* image on every render and converts the
    colour mode each time; doing it once here keeps repaints cheap and caps the
    memory each cached cover holds.
    """
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > _MAX_EDGE:
        scale = _MAX_EDGE / longest
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), PILImage.LANCZOS)
    img.load()
    return img


def _read_disk(path: Path) -> PILImage.Image | None:
    try:
        img = PILImage.open(path)
        img.load()
        return img
    except Exception:
        return None


def _write_disk(img: PILImage.Image, path: Path) -> None:
    try:
        out = img if img.mode == "RGB" else img.convert("RGB")
        tmp = path.with_name(path.name + ".tmp")
        out.save(tmp, "JPEG", quality=85)
        os.replace(tmp, path)
    except Exception:
        pass


def _decode_bytes(data: bytes) -> PILImage.Image | None:
    try:
        return _normalize(PILImage.open(io.BytesIO(data)))
    except Exception:
        return None


async def fetch_image(url: str) -> PILImage.Image | None:
    cached = _IMAGE_CACHE.get(url)
    if cached is not None:
        return cached
    async with _FETCH_SEM:
        # Re-check after acquiring: another worker may have fetched it while
        # we were waiting.
        cached = _IMAGE_CACHE.get(url)
        if cached is not None:
            return cached

        # Decode/IO is blocking CPU work; keep it off the UI event loop so a
        # grid full of covers doesn't stall keystrokes.
        path = _disk_path(url)
        if path is not None and path.exists():
            img = await asyncio.to_thread(_read_disk, path)
            if img is not None:
                _IMAGE_CACHE[url] = img
                return img

        try:
            r = await _get_client().get(url)
            r.raise_for_status()
            data = r.content
        except Exception:
            return None

        img = await asyncio.to_thread(_decode_bytes, data)
        if img is None:
            return None
        _IMAGE_CACHE[url] = img
        if path is not None:
            await asyncio.to_thread(_write_disk, img, path)
        return img
