"""MPRIS2 backend (audio/mpris.py): state logic, and a real round trip over a
private dbus-daemon when one is installed (never the user's session bus)."""

import asyncio
import shutil
import subprocess
import threading

import pytest

pytest.importorskip("dbus_fast")  # a Linux-only dependency

from dbus_fast import Variant
from dbus_fast.aio import MessageBus

from qobit.audio.mpris import (
    BUS_NAME,
    OBJECT_PATH,
    MPRISBackend,
    PlayerInterface,
    PlayerState,
)
from qobit.qobuz.models import Track


def _track(**kw) -> Track:
    base = {
        "id": "1",
        "title": "So What",
        "artist": "Miles Davis",
        "album": "Kind of Blue",
        "album_id": "a1",
        "duration": 545,
        "image_url": "https://example.test/cover.jpg",
    }
    base.update(kw)
    return Track(**base)


class _Calls:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.event = threading.Event()

    def cb(self, name: str):
        def _f() -> None:
            self.names.append(name)
            self.event.set()

        return _f


def _player(calls: _Calls) -> PlayerInterface:
    return PlayerInterface(calls.cb("play_pause"), calls.cb("next"), calls.cb("previous"))


# ── state ──


def test_status():
    assert PlayerState().status == "Stopped"
    assert PlayerState(is_playing=True).status == "Playing"
    assert PlayerState(is_playing=True, is_paused=True).status == "Paused"


def test_metadata_without_track():
    meta = PlayerState().metadata()
    assert meta == {"mpris:trackid": Variant("o", "/org/mpris/MediaPlayer2/TrackList/NoTrack")}


def test_metadata_with_track():
    t = _track()
    meta = PlayerState(track=t, duration_us=545_000_000).metadata()
    assert meta["xesam:title"] == Variant("s", t.display_title)
    assert meta["xesam:artist"] == Variant("as", ["Miles Davis"])
    assert meta["xesam:album"] == Variant("s", "Kind of Blue")
    assert meta["mpris:length"] == Variant("x", 545_000_000)
    assert meta["mpris:artUrl"] == Variant("s", "https://example.test/cover.jpg")


def test_metadata_without_art():
    assert "mpris:artUrl" not in PlayerState(track=_track(image_url="")).metadata()


def test_set_state_emits_only_on_visible_change(monkeypatch):
    p = _player(_Calls())
    emitted: list[dict] = []
    monkeypatch.setattr(p, "emit_properties_changed", emitted.append)
    t = _track()

    p.set_state(PlayerState(track=t, is_playing=True))
    assert len(emitted) == 1
    assert emitted[0]["PlaybackStatus"] == "Playing"
    # Position alone doesn't change what a client shows.
    p.set_state(PlayerState(track=t, is_playing=True, position_us=5_000_000))
    assert len(emitted) == 1
    p.set_state(PlayerState(track=t, is_playing=True, is_paused=True))
    assert emitted[-1]["PlaybackStatus"] == "Paused"
    p.set_state(PlayerState(track=_track(id="2"), is_playing=True))
    assert len(emitted) == 3


@pytest.mark.parametrize(
    ("playing", "paused", "method", "toggles"),
    [
        (False, False, "Play", True),
        (True, True, "Play", True),
        (True, False, "Play", False),
        (True, False, "Pause", True),
        (True, True, "Pause", False),
        (False, False, "Pause", False),
    ],
)
def test_play_and_pause_only_toggle_when_needed(playing, paused, method, toggles):
    calls = _Calls()
    p = _player(calls)
    p.state = PlayerState(track=_track(), is_playing=playing, is_paused=paused)
    getattr(p, method)()
    assert calls.names == (["play_pause"] if toggles else [])


# ── over a private bus ──


@pytest.fixture
def bus_address():
    daemon = shutil.which("dbus-daemon")
    if daemon is None:
        pytest.skip("dbus-daemon not installed")
    proc = subprocess.Popen(
        [daemon, "--session", "--nofork", "--nopidfile", "--print-address=1"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        address = proc.stdout.readline().strip()
        if not address:
            pytest.skip("dbus-daemon didn't start")
        yield address
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _backend(address: str, calls: _Calls) -> MPRISBackend:
    b = MPRISBackend(
        calls.cb("play_pause"), calls.cb("next"), calls.cb("previous"), bus_address=address
    )
    assert b.ready.wait(5)
    return b


async def _client(address: str, bus_name: str):
    bus = await MessageBus(bus_address=address).connect()
    intro = await bus.introspect(bus_name, OBJECT_PATH)
    obj = bus.get_proxy_object(bus_name, OBJECT_PATH, intro)
    return bus, obj


def test_round_trip(bus_address):
    calls = _Calls()
    backend = _backend(bus_address, calls)
    try:
        assert backend.bus_name == BUS_NAME

        async def run() -> None:
            bus, obj = await _client(bus_address, BUS_NAME)
            root = obj.get_interface("org.mpris.MediaPlayer2")
            player = obj.get_interface("org.mpris.MediaPlayer2.Player")
            props = obj.get_interface("org.freedesktop.DBus.Properties")
            assert await root.get_identity() == "qobit"
            assert await player.get_playback_status() == "Stopped"

            changed = asyncio.Event()
            seen: dict = {}

            def on_changed(iface, values, _invalidated):
                seen.update(values)
                changed.set()

            props.on_properties_changed(on_changed)
            backend.update(_track(), True, False, 3.0, 545.0)
            await asyncio.wait_for(changed.wait(), 5)
            assert seen["PlaybackStatus"].value == "Playing"
            assert seen["Metadata"].value["xesam:title"].value == "So What"

            meta = await player.get_metadata()
            assert meta["mpris:length"].value == 545_000_000
            assert await player.get_position() == 3_000_000

            await player.call_next()
            await player.call_play_pause()
            await player.call_play()  # already playing: no toggle
            bus.disconnect()

        asyncio.run(run())
        assert calls.names == ["next", "play_pause"]
    finally:
        backend.close()
    assert not backend._thread.is_alive()


def test_second_instance_takes_an_instance_name(bus_address):
    first = _backend(bus_address, _Calls())
    second = _backend(bus_address, _Calls())
    try:
        assert first.bus_name == BUS_NAME
        assert second.bus_name is not None
        assert second.bus_name.startswith(f"{BUS_NAME}.instance")
    finally:
        second.close()
        first.close()


def test_no_bus_degrades_silently():
    calls = _Calls()
    backend = _backend("unix:path=/nonexistent/qobit-test-bus", calls)
    assert backend.bus_name is None
    # Calls after a failed connect are harmless no-ops.
    backend.update(_track(), True, False, 0.0, 1.0)
    backend.close()
