"""
Linux MPRIS2 over D-Bus, with dbus-fast (pure Python, asyncio).

The bus runs on its own asyncio loop in a daemon thread, so the app sees the
same contract as before: `update()` / `close()` are called from the app's
thread, and the transport callbacks fire on the D-Bus thread (the app wraps
them in `call_from_thread`). All interface state is touched only on the D-Bus
loop; `update()` hands a snapshot over with `call_soon_threadsafe`.

Startup never blocks the app: the connection is made in the thread, and any
failure (no session bus, no dbus-fast) just leaves media keys off.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from dbus_fast import BusType, NameFlag, PropertyAccess, RequestNameReply, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.annotations import (
    DBusBool,
    DBusDict,
    DBusDouble,
    DBusInt64,
    DBusObjectPath,
    DBusSignature,
    DBusStr,
)
from dbus_fast.service import ServiceInterface, dbus_method, dbus_property, dbus_signal

if TYPE_CHECKING:
    from ..qobuz.models import Track

DBusStrList = Annotated[list[str], DBusSignature("as")]

OBJECT_PATH = "/org/mpris/MediaPlayer2"
BUS_NAME = "org.mpris.MediaPlayer2.qobit"
_NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
_CURRENT_TRACK = "/org/mpris/MediaPlayer2/CurrentTrack"


@dataclass(frozen=True)
class PlayerState:
    track: Track | None = None
    is_playing: bool = False
    is_paused: bool = False
    position_us: int = 0
    duration_us: int = 0

    @property
    def status(self) -> str:
        if not self.is_playing:
            return "Stopped"
        return "Paused" if self.is_paused else "Playing"

    def metadata(self) -> dict[str, Variant]:
        t = self.track
        if t is None:
            return {"mpris:trackid": Variant("o", _NO_TRACK)}
        meta = {
            "mpris:trackid": Variant("o", _CURRENT_TRACK),
            "xesam:title": Variant("s", t.display_title),
            "xesam:artist": Variant("as", [t.artist]),
            "xesam:album": Variant("s", t.album),
            "mpris:length": Variant("x", self.duration_us),
        }
        if t.image_url:
            meta["mpris:artUrl"] = Variant("s", t.image_url)
        return meta


class RootInterface(ServiceInterface):
    """org.mpris.MediaPlayer2: identity only (qobit can't raise or quit)."""

    def __init__(self) -> None:
        super().__init__("org.mpris.MediaPlayer2")

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> DBusBool:
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> DBusBool:
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> DBusBool:
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> DBusStr:
        return "qobit"

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> DBusStrList:
        return []

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> DBusStrList:
        return ["audio/flac", "audio/ogg"]

    @dbus_method()
    def Raise(self):
        pass

    @dbus_method()
    def Quit(self):
        pass


class PlayerInterface(ServiceInterface):
    """org.mpris.MediaPlayer2.Player: play/pause/next/previous, no seeking.

    Loop, shuffle, rate and volume are fixed: qobit never changes the samples
    (no software volume), so their setters accept and ignore a value.
    """

    def __init__(
        self,
        on_play_pause: Callable[[], None],
        on_next: Callable[[], None],
        on_previous: Callable[[], None],
    ) -> None:
        super().__init__("org.mpris.MediaPlayer2.Player")
        self._on_play_pause = on_play_pause
        self._on_next = on_next
        self._on_previous = on_previous
        self.state = PlayerState()

    def set_state(self, state: PlayerState) -> None:
        """Take a new snapshot; tell clients when what they show changed."""
        old, self.state = self.state, state
        if (
            state.track is not old.track
            or state.is_playing != old.is_playing
            or state.is_paused != old.is_paused
        ):
            self.emit_properties_changed(
                {"Metadata": state.metadata(), "PlaybackStatus": state.status}
            )

    # ── properties ──

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> DBusStr:
        return self.state.status

    @dbus_property()
    def LoopStatus(self) -> DBusStr:
        return "None"

    @LoopStatus.setter
    def LoopStatus(self, value: DBusStr) -> None:
        pass

    @dbus_property()
    def Rate(self) -> DBusDouble:
        return 1.0

    @Rate.setter
    def Rate(self, value: DBusDouble) -> None:
        pass

    @dbus_property()
    def Shuffle(self) -> DBusBool:
        return False

    @Shuffle.setter
    def Shuffle(self, value: DBusBool) -> None:
        pass

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> DBusDict:
        return self.state.metadata()

    @dbus_property()
    def Volume(self) -> DBusDouble:
        return 1.0

    @Volume.setter
    def Volume(self, value: DBusDouble) -> None:
        pass

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> DBusInt64:
        return self.state.position_us

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> DBusDouble:
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> DBusDouble:
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> DBusBool:
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> DBusBool:
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> DBusBool:
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> DBusBool:
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> DBusBool:
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> DBusBool:
        return True

    # ── methods ──

    @dbus_method()
    def Next(self):
        self._on_next()

    @dbus_method()
    def Previous(self):
        self._on_previous()

    @dbus_method()
    def PlayPause(self):
        self._on_play_pause()

    @dbus_method()
    def Play(self):
        if self.state.status != "Playing":
            self._on_play_pause()

    @dbus_method()
    def Pause(self):
        if self.state.status == "Playing":
            self._on_play_pause()

    @dbus_method()
    def Stop(self):
        pass

    @dbus_method()
    def Seek(self, offset: DBusInt64):
        pass

    @dbus_method()
    def SetPosition(self, track_id: DBusObjectPath, position: DBusInt64):
        pass

    @dbus_method()
    def OpenUri(self, uri: DBusStr):
        pass

    @dbus_signal()
    def Seeked(self) -> DBusInt64:
        return self.state.position_us


class MPRISBackend:
    """MPRIS2 on the session bus, served from a background thread."""

    def __init__(
        self,
        on_play_pause: Callable[[], None],
        on_next: Callable[[], None],
        on_previous: Callable[[], None],
        *,
        bus_address: str | None = None,
    ) -> None:
        self._root = RootInterface()
        self._player = PlayerInterface(on_play_pause, on_next, on_previous)
        self._bus_address = bus_address
        self._bus: MessageBus | None = None
        self.bus_name: str | None = None  # set once the name is owned
        self.ready = threading.Event()  # set when connected, or when that failed
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="qobit-mpris", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect())
        except Exception:
            self._bus = None  # no session bus, name refused…: media keys stay off
        finally:
            self.ready.set()
        if self._bus is None:
            self._loop.close()
            return
        self._loop.run_forever()
        with contextlib.suppress(Exception):
            self._loop.close()

    async def _connect(self) -> None:
        bus = await MessageBus(bus_address=self._bus_address, bus_type=BusType.SESSION).connect()
        bus.export(OBJECT_PATH, self._root)
        bus.export(OBJECT_PATH, self._player)
        # A second qobit takes an instance name (allowed by the spec) instead
        # of queueing for the first one's.
        for name in (BUS_NAME, f"{BUS_NAME}.instance{os.getpid()}"):
            reply = await bus.request_name(name, NameFlag.DO_NOT_QUEUE)
            if reply in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
                self.bus_name = name
                break
        else:
            bus.disconnect()
            raise RuntimeError("no MPRIS bus name available")
        self._bus = bus

    def _call(self, fn: Callable[..., object], *args: object) -> None:
        """Run fn(*args) on the D-Bus loop; a no-op once it has stopped."""
        if self._bus is None or self._loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):  # loop closed between the check and here
            self._loop.call_soon_threadsafe(fn, *args)

    def update(
        self,
        track: Track | None,
        is_playing: bool,
        is_paused: bool,
        position: float,
        duration: float,
    ) -> None:
        state = PlayerState(
            track=track,
            is_playing=is_playing,
            is_paused=is_paused,
            position_us=int(position * 1_000_000),
            duration_us=int(duration * 1_000_000),
        )
        if self._bus is None:
            # Not connected yet: keep the latest state so the first read is right.
            self._player.state = state
            return
        self._call(self._player.set_state, state)

    def set_artwork(self, image: object) -> None:
        pass  # MPRIS clients load the cover from Metadata's mpris:artUrl

    def close(self) -> None:
        bus = self._bus
        self._bus = None
        if bus is None or self._loop.is_closed():
            return

        def _stop() -> None:
            with contextlib.suppress(Exception):
                bus.disconnect()
            self._loop.stop()

        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(_stop)
        self._thread.join(timeout=1.0)
