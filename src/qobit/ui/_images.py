from __future__ import annotations

import asyncio
import hashlib
import io
import os
from collections import OrderedDict
from pathlib import Path

import httpx
from PIL import Image as PILImage

# Cap concurrent HTTP fetches so a full library grid doesn't flood the
# network or thrash PIL/Kitty rendering all at once.
_FETCH_SEM = asyncio.Semaphore(6)

# URL → decoded PIL Image, bounded LRU.  Evicts the least-recently-used entry
# once the limit is hit so long browsing sessions don't accumulate hundreds of
# MB of PIL objects.  200 entries at ~600×600 JPEG-decoded ≈ 40–80 MB ceiling.
_IMAGE_CACHE_MAX = 200
_IMAGE_CACHE: OrderedDict[str, PILImage.Image] = OrderedDict()


def _cache_get(url: str) -> PILImage.Image | None:
    img = _IMAGE_CACHE.get(url)
    if img is not None:
        _IMAGE_CACHE.move_to_end(url)
    return img


def _cache_put(url: str, img: PILImage.Image) -> None:
    _IMAGE_CACHE[url] = img
    _IMAGE_CACHE.move_to_end(url)
    if len(_IMAGE_CACHE) > _IMAGE_CACHE_MAX:
        _IMAGE_CACHE.popitem(last=False)


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


def prune_disk_cache(max_bytes: int = 200 * 1024 * 1024) -> None:
    """Delete the oldest cached cover files until total size is under max_bytes.

    Called at startup so long sessions don't let the disk cache grow unbounded.
    Default limit: 200 MB.  Silently no-ops if the cache dir is unavailable.
    """
    d = _cache_dir()
    if d is None:
        return
    try:
        entries = [(p.stat().st_mtime, p.stat().st_size, p) for p in d.glob("*.jpg") if p.is_file()]
    except Exception:
        return
    total = sum(s for _, s, _ in entries)
    if total <= max_bytes:
        return
    entries.sort()  # oldest (lowest mtime) first
    for _mtime, size, path in entries:
        if total <= max_bytes:
            break
        try:
            path.unlink()
            total -= size
        except Exception:
            pass


def clear_disk_cache() -> tuple[Path | None, int, int]:
    """Delete all cached cover art from disk.

    Returns ``(cache_dir, files_removed, bytes_freed)``; ``cache_dir`` is
    ``None`` if the cache location is unavailable.
    """
    d = _cache_dir()
    if d is None:
        return None, 0, 0
    removed = 0
    freed = 0
    for p in d.glob("*"):
        try:
            if p.is_file():
                freed += p.stat().st_size
                p.unlink()
                removed += 1
        except Exception:
            pass
    return d, removed, freed


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
    cached = _cache_get(url)
    if cached is not None:
        return cached
    async with _FETCH_SEM:
        # Re-check after acquiring: another worker may have fetched it while
        # we were waiting.
        cached = _cache_get(url)
        if cached is not None:
            return cached

        # Decode/IO is blocking CPU work; keep it off the UI event loop so a
        # grid full of covers doesn't stall keystrokes.
        path = _disk_path(url)
        if path is not None and path.exists():
            img = await asyncio.to_thread(_read_disk, path)
            if img is not None:
                _cache_put(url, img)
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
        _cache_put(url, img)
        if path is not None:
            await asyncio.to_thread(_write_disk, img, path)
        return img
