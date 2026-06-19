"""
OS media control integration.

macOS  — MPRemoteCommandCenter + MPNowPlayingInfoCenter
         requires: pyobjc-framework-MediaPlayer  (pip install pyobjc-framework-MediaPlayer)

Linux  — MPRIS2 over D-Bus
         requires: pydbus + python3-gi system package
         (pip install pydbus  &&  sudo apt install python3-gi / dnf install python3-gobject)

Both backends degrade silently when the required packages are not installed.
"""

from __future__ import annotations

import platform
import threading
from typing import TYPE_CHECKING, Callable

_SYSTEM = platform.system()

if TYPE_CHECKING:
    from ..qobuz.models import Track


class MediaKeys:
    """Unified OS media controls — macOS Now Playing / Linux MPRIS2."""

    def __init__(
        self,
        on_play_pause: Callable[[], None],
        on_next: Callable[[], None],
        on_previous: Callable[[], None],
    ) -> None:
        self._backend: _MacOSBackend | _MPRISBackend | None = None
        try:
            if _SYSTEM == "Darwin":
                self._backend = _MacOSBackend(on_play_pause, on_next, on_previous)
            elif _SYSTEM == "Linux":
                self._backend = _MPRISBackend(on_play_pause, on_next, on_previous)
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
            try:
                self._backend.update(track, is_playing, is_paused, position, duration)
            except Exception:
                pass

    def close(self) -> None:
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:
                pass
            self._backend = None


# ── macOS backend ─────────────────────────────────────────────────────────────


class _MacOSBackend:
    def __init__(
        self,
        on_play_pause: Callable[[], None],
        on_next: Callable[[], None],
        on_previous: Callable[[], None],
    ) -> None:
        from MediaPlayer import (  # type: ignore[import]
            MPMediaItemPropertyAlbumTitle,
            MPMediaItemPropertyArtist,
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
        cc.togglePlayPauseCommand().enabled = True
        cc.nextTrackCommand().enabled = True
        cc.previousTrackCommand().enabled = True

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
        self._np_center.setNowPlayingInfo_(info)

    def close(self) -> None:
        self._np_center.setNowPlayingInfo_(None)


# ── Linux MPRIS2 backend ──────────────────────────────────────────────────────


class _MPRISBackend:
    dbus = """
    <node>
      <interface name='org.mpris.MediaPlayer2'>
        <property name='CanQuit'       type='b'  access='read'/>
        <property name='CanRaise'      type='b'  access='read'/>
        <property name='HasTrackList'  type='b'  access='read'/>
        <property name='Identity'      type='s'  access='read'/>
        <property name='SupportedUriSchemes' type='as' access='read'/>
        <property name='SupportedMimeTypes'  type='as' access='read'/>
        <method name='Raise'/>
        <method name='Quit'/>
      </interface>
      <interface name='org.mpris.MediaPlayer2.Player'>
        <property name='PlaybackStatus' type='s'    access='read'/>
        <property name='LoopStatus'     type='s'    access='readwrite'/>
        <property name='Rate'           type='d'    access='readwrite'/>
        <property name='Shuffle'        type='b'    access='readwrite'/>
        <property name='Metadata'       type='a{sv}' access='read'/>
        <property name='Volume'         type='d'    access='readwrite'/>
        <property name='Position'       type='x'    access='read'/>
        <property name='MinimumRate'    type='d'    access='read'/>
        <property name='MaximumRate'    type='d'    access='read'/>
        <property name='CanGoNext'      type='b'    access='read'/>
        <property name='CanGoPrevious'  type='b'    access='read'/>
        <property name='CanPlay'        type='b'    access='read'/>
        <property name='CanPause'       type='b'    access='read'/>
        <property name='CanSeek'        type='b'    access='read'/>
        <property name='CanControl'     type='b'    access='read'/>
        <method name='Next'/>
        <method name='Previous'/>
        <method name='Pause'/>
        <method name='PlayPause'/>
        <method name='Stop'/>
        <method name='Play'/>
        <method name='Seek'>
          <arg direction='in' type='x' name='Offset'/>
        </method>
        <method name='SetPosition'>
          <arg direction='in' type='o' name='TrackId'/>
          <arg direction='in' type='x' name='Position'/>
        </method>
        <method name='OpenUri'>
          <arg direction='in' type='s' name='Uri'/>
        </method>
        <signal name='Seeked'>
          <arg type='x' name='Position'/>
        </signal>
      </interface>
    </node>
    """

    # org.mpris.MediaPlayer2 constants
    CanQuit = False
    CanRaise = False
    HasTrackList = False
    Identity = "qobit"
    SupportedUriSchemes: list[str] = []
    SupportedMimeTypes = ["audio/flac", "audio/ogg"]

    def __init__(
        self,
        on_play_pause: Callable[[], None],
        on_next: Callable[[], None],
        on_previous: Callable[[], None],
    ) -> None:
        from gi.repository import GLib  # type: ignore[import]
        from pydbus import SessionBus  # type: ignore[import]

        self._on_play_pause = on_play_pause
        self._on_next = on_next
        self._on_previous = on_previous
        self._GLib = GLib

        self._lock = threading.Lock()
        self._track: Track | None = None
        self._is_playing = False
        self._is_paused = False
        self._position_us: int = 0
        self._duration_us: int = 0

        self._loop = GLib.MainLoop()
        bus = SessionBus()
        self._pub = bus.publish("org.mpris.MediaPlayer2.qobit", ("/org/mpris/MediaPlayer2", self))

        t = threading.Thread(target=self._loop.run, daemon=True)
        t.start()

    # ── org.mpris.MediaPlayer2.Player properties ──────────────────────────────

    @property
    def PlaybackStatus(self) -> str:
        if not self._is_playing:
            return "Stopped"
        return "Paused" if self._is_paused else "Playing"

    @property
    def LoopStatus(self) -> str:
        return "None"

    @LoopStatus.setter
    def LoopStatus(self, value: str) -> None:
        pass

    @property
    def Rate(self) -> float:
        return 1.0

    @Rate.setter
    def Rate(self, value: float) -> None:
        pass

    @property
    def Shuffle(self) -> bool:
        return False

    @Shuffle.setter
    def Shuffle(self, value: bool) -> None:
        pass

    @property
    def Metadata(self) -> dict:
        GLib = self._GLib
        if not self._track:
            return {"mpris:trackid": GLib.Variant("o", "/org/mpris/MediaPlayer2/NoTrack")}
        return {
            "mpris:trackid": GLib.Variant("o", "/org/mpris/MediaPlayer2/CurrentTrack"),
            "xesam:title": GLib.Variant("s", self._track.display_title),
            "xesam:artist": GLib.Variant("as", [self._track.artist]),
            "xesam:album": GLib.Variant("s", self._track.album),
            "mpris:length": GLib.Variant("x", self._duration_us),
        }

    @property
    def Volume(self) -> float:
        return 1.0

    @Volume.setter
    def Volume(self, value: float) -> None:
        pass

    @property
    def Position(self) -> int:
        return self._position_us

    MinimumRate = 1.0
    MaximumRate = 1.0
    CanGoNext = True
    CanGoPrevious = True
    CanPlay = True
    CanPause = True
    CanSeek = False
    CanControl = True

    # ── org.mpris.MediaPlayer2.Player methods ─────────────────────────────────

    def Raise(self) -> None:
        pass

    def Quit(self) -> None:
        pass

    def Next(self) -> None:
        self._on_next()

    def Previous(self) -> None:
        self._on_previous()

    def PlayPause(self) -> None:
        self._on_play_pause()

    def Play(self) -> None:
        if not self._is_playing or self._is_paused:
            self._on_play_pause()

    def Pause(self) -> None:
        if self._is_playing and not self._is_paused:
            self._on_play_pause()

    def Stop(self) -> None:
        pass

    def Seek(self, offset: int) -> None:
        pass

    def SetPosition(self, track_id: str, position: int) -> None:
        pass

    def OpenUri(self, uri: str) -> None:
        pass

    # ── state update from app ─────────────────────────────────────────────────

    def update(
        self,
        track: Track | None,
        is_playing: bool,
        is_paused: bool,
        position: float,
        duration: float,
    ) -> None:
        with self._lock:
            self._track = track
            self._is_playing = is_playing
            self._is_paused = is_paused
            self._position_us = int(position * 1_000_000)
            self._duration_us = int(duration * 1_000_000)

    def close(self) -> None:
        try:
            self._pub.unpublish()
        except Exception:
            pass
        self._loop.quit()
