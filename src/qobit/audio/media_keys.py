"""
OS media control integration.

macOS  — MPRemoteCommandCenter + MPNowPlayingInfoCenter
         requires: pyobjc-framework-MediaPlayer (a default dependency on darwin)

Linux  — MPRIS2 over D-Bus (audio/mpris.py)
         requires: dbus-fast (a default dependency on linux; pure Python)

Both backends degrade silently when the required packages are not installed.
"""

from __future__ import annotations

import contextlib
import platform
from collections.abc import Callable
from typing import TYPE_CHECKING

_SYSTEM = platform.system()

if TYPE_CHECKING:
    from PIL import Image as PILImage

    from ..qobuz.models import Track
    from .mpris import MPRISBackend


class MediaKeys:
    """Unified OS media controls — macOS Now Playing / Linux MPRIS2."""

    def __init__(
        self,
        on_play_pause: Callable[[], None],
        on_next: Callable[[], object],
        on_previous: Callable[[], None],
    ) -> None:
        self._backend: _MacOSBackend | MPRISBackend | None = None
        try:
            if _SYSTEM == "Darwin":
                self._backend = _MacOSBackend(on_play_pause, on_next, on_previous)
            elif _SYSTEM == "Linux":
                from .mpris import MPRISBackend

                self._backend = MPRISBackend(on_play_pause, on_next, on_previous)
        except Exception:
            pass  # silently degrade — app works fine without OS integration

    def update(
        self,
        track: Track | None,
        is_playing: bool,
        is_paused: bool,
        position: float,
        duration: float,
    ) -> None:
        if self._backend is not None:
            with contextlib.suppress(Exception):
                self._backend.update(track, is_playing, is_paused, position, duration)

    def set_artwork(self, image: PILImage.Image) -> None:
        """Pass a PIL.Image.Image once it has been fetched."""
        if self._backend is not None:
            with contextlib.suppress(Exception):
                self._backend.set_artwork(image)

    def close(self) -> None:
        if self._backend is not None:
            with contextlib.suppress(Exception):
                self._backend.close()
            self._backend = None


# ── macOS backend ─────────────────────────────────────────────────────────────


class _MacOSBackend:
    def __init__(
        self,
        on_play_pause: Callable[[], None],
        on_next: Callable[[], object],
        on_previous: Callable[[], None],
    ) -> None:
        from MediaPlayer import (
            MPMediaItemPropertyAlbumTitle,
            MPMediaItemPropertyArtist,
            MPMediaItemPropertyArtwork,
            MPMediaItemPropertyPlaybackDuration,
            MPMediaItemPropertyTitle,
            MPNowPlayingInfoCenter,
            MPNowPlayingInfoPropertyElapsedPlaybackTime,
            MPNowPlayingInfoPropertyPlaybackRate,
            MPRemoteCommandCenter,
            MPRemoteCommandHandlerStatusSuccess,
        )

        self._np_center = MPNowPlayingInfoCenter.defaultCenter()
        self._keys = {
            "title": MPMediaItemPropertyTitle,
            "artist": MPMediaItemPropertyArtist,
            "album": MPMediaItemPropertyAlbumTitle,
            "duration": MPMediaItemPropertyPlaybackDuration,
            "elapsed": MPNowPlayingInfoPropertyElapsedPlaybackTime,
            "rate": MPNowPlayingInfoPropertyPlaybackRate,
            "artwork": MPMediaItemPropertyArtwork,
        }
        _ok = MPRemoteCommandHandlerStatusSuccess

        # ObjC blocks must stay alive — keep references
        def _toggle(event: object) -> int:
            on_play_pause()
            return _ok

        def _next(event: object) -> int:
            on_next()
            return _ok

        def _previous(event: object) -> int:
            on_previous()
            return _ok

        self._handlers = (_toggle, _next, _previous)

        cc = MPRemoteCommandCenter.sharedCommandCenter()
        cc.togglePlayPauseCommand().addTargetWithHandler_(_toggle)
        cc.nextTrackCommand().addTargetWithHandler_(_next)
        cc.previousTrackCommand().addTargetWithHandler_(_previous)
        # Explicit setter: pyobjc 12 no longer exposes ObjC properties as
        # assignable Python attributes (`.enabled = True` raises).
        cc.togglePlayPauseCommand().setEnabled_(True)
        cc.nextTrackCommand().setEnabled_(True)
        cc.previousTrackCommand().setEnabled_(True)

        self._current_info: dict | None = None
        self._artwork: object = None
        self._artwork_handler: object = None  # keep ObjC block alive

    def update(
        self,
        track: Track | None,
        is_playing: bool,
        is_paused: bool,
        position: float,
        duration: float,
    ) -> None:
        if track is None:
            self._np_center.setNowPlayingInfo_(None)
            self._current_info = None
            self._artwork = None
            self._artwork_handler = None
            return
        k = self._keys
        info: dict = {
            k["title"]: track.display_title,
            k["artist"]: track.artist,
            k["album"]: track.album,
            k["elapsed"]: position,
            k["rate"]: 0.0 if is_paused else (1.0 if is_playing else 0.0),
        }
        if duration:
            info[k["duration"]] = duration
        if self._artwork is not None:
            info[k["artwork"]] = self._artwork
        self._current_info = info
        self._np_center.setNowPlayingInfo_(info)

    def set_artwork(self, image: PILImage.Image) -> None:
        import io

        from AppKit import NSImage
        from Foundation import NSData, NSMakeSize
        from MediaPlayer import MPMediaItemArtwork

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        raw = buf.getvalue()
        ns_data = NSData.dataWithBytes_length_(raw, len(raw))
        ns_image = NSImage.alloc().initWithData_(ns_data)

        def _handler(size: object) -> object:
            return ns_image

        self._artwork_handler = _handler  # prevent GC of the ObjC block
        self._artwork = MPMediaItemArtwork.alloc().initWithBoundsSize_requestHandler_(
            NSMakeSize(800.0, 800.0), _handler
        )

        if self._current_info is not None:
            self._current_info[self._keys["artwork"]] = self._artwork
            self._np_center.setNowPlayingInfo_(self._current_info)

    def close(self) -> None:
        self._np_center.setNowPlayingInfo_(None)
