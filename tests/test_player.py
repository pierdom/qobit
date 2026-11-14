"""MpvPlayer end-of-track bookkeeping (no real mpv: Popen is faked).

Regression for a race found live: stop() used to bump _stop_gen *before*
terminating mpv, so the poll thread could record the new generation while the
old process still looked alive — and then call the stop a natural end, which
skipped to the next track. snapshot() must only ever report
(old gen, running) or (new gen, not running).
"""

from __future__ import annotations

import pytest

import qobit.audio.player as player_module
from qobit.audio.player import MpvPlayer


class _FakeProc:
    def __init__(self, *args, **kwargs):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


@pytest.fixture
def player(monkeypatch, tmp_path):
    monkeypatch.setattr(player_module.subprocess, "Popen", _FakeProc)
    p = MpvPlayer()
    p._sock = tmp_path / "mpv.sock"
    return p


def test_snapshot_after_play_is_running_with_current_gen(player):
    player.play("u")
    gen, running = player.snapshot()
    assert running and gen == player._stop_gen


def test_stop_changes_gen_and_running_together(player):
    player.play("u")
    gen_before, _ = player.snapshot()
    proc = player._proc
    # Simulate the old race window: the process is still alive at the moment
    # stop() bumps the generation. snapshot() must already say "not running".
    player.stop()
    gen_after, running = player.snapshot()
    assert gen_after == gen_before + 1 and not running
    assert proc.returncode is not None  # and the process was terminated


def test_natural_exit_keeps_gen(player):
    player.play("u")
    gen_before, _ = player.snapshot()
    player._proc.returncode = 0  # mpv reached end of file on its own
    gen_after, running = player.snapshot()
    assert gen_after == gen_before and not running
