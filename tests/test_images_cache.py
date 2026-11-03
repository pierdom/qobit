"""qobit.ui._images — the in-memory LRU cache, the once-at-fetch-time
normalization, and the on-disk cache pruning. conftest.py's autouse fixture
redirects _CACHE_DIR to a tmp dir and resets _IMAGE_CACHE around every test.
"""

from __future__ import annotations

import os

from PIL import Image as PILImage

from qobit.ui import _images

# ── in-memory LRU cache ──────────────────────────────────────────────────────


def test_cache_put_then_get_returns_same_object():
    img = PILImage.new("RGB", (2, 2))
    _images._cache_put("url-a", img)
    assert _images._cache_get("url-a") is img


def test_cache_get_miss_returns_none():
    assert _images._cache_get("never-cached") is None


def test_cache_evicts_least_recently_used_when_over_max(monkeypatch):
    monkeypatch.setattr(_images, "_IMAGE_CACHE_MAX", 3)
    imgs = {k: PILImage.new("RGB", (1, 1)) for k in "abcd"}
    for k in "abc":
        _images._cache_put(k, imgs[k])
    assert list(_images._IMAGE_CACHE.keys()) == ["a", "b", "c"]

    _images._cache_put("d", imgs["d"])  # over max=3 -> evicts "a" (oldest)
    assert list(_images._IMAGE_CACHE.keys()) == ["b", "c", "d"]
    assert _images._cache_get("a") is None


def test_cache_get_marks_entry_as_recently_used(monkeypatch):
    monkeypatch.setattr(_images, "_IMAGE_CACHE_MAX", 3)
    imgs = {k: PILImage.new("RGB", (1, 1)) for k in "abcd"}
    for k in "abc":
        _images._cache_put(k, imgs[k])

    _images._cache_get("a")  # touch "a" so it's no longer the LRU entry
    _images._cache_put("d", imgs["d"])  # should now evict "b", not "a"
    assert _images._cache_get("a") is imgs["a"]
    assert _images._cache_get("b") is None


# ── _normalize (resize + mode) ───────────────────────────────────────────────


def test_normalize_downscales_above_max_edge():
    img = PILImage.new("RGB", (1200, 600))
    out = _images._normalize(img)
    assert max(out.size) == _images._MAX_EDGE
    assert out.size == (_images._MAX_EDGE, _images._MAX_EDGE // 2)


def test_normalize_leaves_small_image_size_unchanged():
    img = PILImage.new("RGB", (100, 50))
    out = _images._normalize(img)
    assert out.size == (100, 50)


def test_normalize_converts_palette_mode_to_rgb():
    img = PILImage.new("P", (10, 10))
    out = _images._normalize(img)
    assert out.mode == "RGB"


def test_normalize_preserves_rgba():
    img = PILImage.new("RGBA", (10, 10))
    out = _images._normalize(img)
    assert out.mode == "RGBA"


# ── disk cache ───────────────────────────────────────────────────────────────


def test_disk_path_is_stable_for_same_url():
    p1 = _images._disk_path("http://example.com/a.jpg")
    p2 = _images._disk_path("http://example.com/a.jpg")
    assert p1 == p2


def test_disk_path_differs_for_different_urls():
    p1 = _images._disk_path("http://example.com/a.jpg")
    p2 = _images._disk_path("http://example.com/b.jpg")
    assert p1 != p2


def test_clear_disk_cache_removes_all_files_and_reports_totals(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _images._CACHE_DIR = cache_dir
    (cache_dir / "a.jpg").write_bytes(b"x" * 100)
    (cache_dir / "b.jpg").write_bytes(b"y" * 50)

    path, removed, freed = _images.clear_disk_cache()

    assert path == cache_dir
    assert removed == 2
    assert freed == 150
    assert list(cache_dir.glob("*")) == []


def test_clear_disk_cache_empty_dir_reports_zero():
    cache_dir = _images._cache_dir()
    path, removed, freed = _images.clear_disk_cache()
    assert path == cache_dir
    assert removed == 0
    assert freed == 0


def test_prune_disk_cache_drops_oldest_until_under_limit(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _images._CACHE_DIR = cache_dir
    for i, name in enumerate(["oldest.jpg", "middle.jpg", "newest.jpg"]):
        p = cache_dir / name
        p.write_bytes(b"x" * 100)
        os.utime(p, (i * 1000, i * 1000))

    _images.prune_disk_cache(max_bytes=150)

    remaining = {p.name for p in cache_dir.glob("*.jpg")}
    assert remaining == {"newest.jpg"}


def test_prune_disk_cache_noop_when_under_limit(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _images._CACHE_DIR = cache_dir
    (cache_dir / "a.jpg").write_bytes(b"x" * 10)

    _images.prune_disk_cache(max_bytes=1_000_000)

    assert (cache_dir / "a.jpg").exists()
