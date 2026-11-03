"""QobitApp's timeline+cursor play context (CLAUDE.md: "Timeline + cursor").

This is the trickiest piece of app.py: _history and _play_queue are derived
slice-properties around a single _timeline list + _cursor index, specifically
so that skipping back and forth through the Queue view never drops tracks.
These tests build a QobitApp directly (no Textual run loop) and manipulate
_timeline/_cursor as the real action methods do, to pin down that invariant.

MediaKeys is stubbed out: constructing the real one touches actual OS media
APIs (macOS MPRemoteCommandCenter / Linux MPRIS2 over D-Bus), which is not
something a unit test should do as a side effect.
"""

from __future__ import annotations

import pytest

import qobit.ui.app as app_module
from qobit.qobuz.models import Track


class _NoOpMediaKeys:
    def __init__(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass

    def set_artwork(self, *args, **kwargs):
        pass

    def close(self):
        pass


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(app_module, "MediaKeys", _NoOpMediaKeys)
    return app_module.QobitApp()


def _track(id_: str) -> Track:
    return Track(
        id=id_, title=f"Track {id_}", artist="Artist", album="Album", album_id="1", duration=180
    )


def _ids(tracks: list[Track]) -> list[str]:
    return [t.id for t in tracks]


# ── _history / _play_queue slices ───────────────────────────────────────────


def test_history_and_queue_empty_when_nothing_loaded(app):
    assert app._cursor == -1
    assert app._history == []
    assert app._play_queue == []


def test_history_is_everything_behind_cursor_queue_is_everything_ahead(app):
    tracks = [_track(str(i)) for i in range(5)]
    app._timeline = tracks
    app._cursor = 2
    assert _ids(app._history) == ["0", "1"]
    assert _ids(app._play_queue) == ["3", "4"]
    # the now-playing track itself is in neither slice
    now_playing_id = tracks[app._cursor].id
    assert now_playing_id not in _ids(app._history)
    assert now_playing_id not in _ids(app._play_queue)


def test_history_empty_at_start_of_timeline(app):
    tracks = [_track(str(i)) for i in range(3)]
    app._timeline = tracks
    app._cursor = 0
    assert app._history == []
    assert _ids(app._play_queue) == ["1", "2"]


def test_play_queue_empty_at_end_of_timeline(app):
    tracks = [_track(str(i)) for i in range(3)]
    app._timeline = tracks
    app._cursor = 2
    assert _ids(app._history) == ["0", "1"]
    assert app._play_queue == []


# ── _trim_history ────────────────────────────────────────────────────────────


def test_trim_history_noop_when_under_the_cap(app):
    tracks = [_track(str(i)) for i in range(10)]
    app._timeline = tracks
    app._cursor = 5
    app._trim_history()
    assert len(app._timeline) == 10
    assert app._cursor == 5


def test_trim_history_drops_oldest_past_the_cap_and_shifts_cursor(app, monkeypatch):
    monkeypatch.setattr(app_module, "_HISTORY_MAX", 5)
    tracks = [_track(str(i)) for i in range(10)]
    app._timeline = tracks
    app._cursor = 9  # 9 tracks of history, over the cap of 5
    app._trim_history()

    # cursor stays pointed at the *same* now-playing track...
    assert app._timeline[app._cursor].id == "9"
    # ...and history is bounded to the cap.
    assert len(app._history) == 5
    assert _ids(app._history) == ["4", "5", "6", "7", "8"]


def test_trim_history_preserves_up_next_untouched(app, monkeypatch):
    monkeypatch.setattr(app_module, "_HISTORY_MAX", 2)
    tracks = [_track(str(i)) for i in range(6)]
    app._timeline = tracks
    app._cursor = 4  # history=[0,1,2,3], now=4, up next=[5]
    app._trim_history()
    assert _ids(app._play_queue) == ["5"]


# ── queue_next / queue_last ──────────────────────────────────────────────────


def test_queue_next_inserts_immediately_after_now_playing(app):
    tracks = [_track(str(i)) for i in range(3)]
    app._timeline = tracks
    app._cursor = 1  # now playing "1", up next ["2"]
    new_track = _track("new")

    app.queue_next(new_track)

    assert _ids(app._play_queue) == ["new", "2"]


def test_queue_last_appends_to_the_tail_of_up_next(app):
    tracks = [_track(str(i)) for i in range(3)]
    app._timeline = tracks
    app._cursor = 1
    new_track = _track("new")

    app.queue_last(new_track)

    assert _ids(app._play_queue) == ["2", "new"]


def test_queue_next_and_queue_last_bump_queue_version(app):
    app._timeline = [_track("0")]
    app._cursor = 0
    before = app.queue_version
    app.queue_next(_track("a"))
    assert app.queue_version == before + 1
    app.queue_last(_track("b"))
    assert app.queue_version == before + 2


# ── remove_from_queue ────────────────────────────────────────────────────────


def test_remove_from_queue_removes_the_exact_queued_instance(app):
    """Two Track objects can share an id (e.g. the same song queued twice).
    remove_from_queue must remove the specific instance the caller is
    holding, not just the first row matching that id."""
    dup_a = _track("dup")
    dup_b = _track("dup")
    app._timeline = [_track("0"), dup_a, dup_b]
    app._cursor = 0

    removed = app.remove_from_queue(dup_b)

    assert removed is True
    assert app._play_queue == [dup_a]  # dup_a (not dup_b) survives, by identity


def test_remove_from_queue_falls_back_to_id_match_if_identity_lost(app):
    """If the exact instance isn't found (e.g. the row was rebuilt from the
    same track id), falls back to removing the first id match ahead of the
    cursor rather than silently doing nothing."""
    app._timeline = [_track("0"), _track("dup")]
    app._cursor = 0
    lookalike = _track("dup")  # a different instance, same id

    removed = app.remove_from_queue(lookalike)

    assert removed is True
    assert app._play_queue == []


def test_remove_from_queue_cannot_remove_a_history_track(app):
    """Up Next only — a track already played (behind the cursor) must not be
    removable via this path even if the same id/instance reappears there."""
    history_track = _track("played")
    app._timeline = [history_track, _track("now")]
    app._cursor = 1

    removed = app.remove_from_queue(history_track)

    assert removed is False
    assert app._timeline[0] is history_track  # untouched


def test_remove_from_queue_returns_false_when_not_present(app):
    app._timeline = [_track("0"), _track("1")]
    app._cursor = 0
    assert app.remove_from_queue(_track("not-queued")) is False


# ── clear_queue / clear_history ──────────────────────────────────────────────


def test_clear_queue_drops_only_up_next(app):
    tracks = [_track(str(i)) for i in range(4)]
    app._timeline = tracks
    app._cursor = 1  # history=[0], now=1, up next=[2,3]

    app.clear_queue()

    assert _ids(app._history) == ["0"]
    assert app._play_queue == []
    assert app._timeline[app._cursor].id == "1"


def test_clear_queue_noop_when_already_empty_does_not_bump_version(app):
    app._timeline = [_track("0")]
    app._cursor = 0
    before = app.queue_version
    app.clear_queue()
    assert app.queue_version == before


def test_clear_history_drops_only_the_past_and_resets_cursor_to_head(app):
    tracks = [_track(str(i)) for i in range(4)]
    app._timeline = tracks
    app._cursor = 2  # history=[0,1], now=2, up next=[3]

    app.clear_history()

    assert app._cursor == 0
    assert app._timeline[app._cursor].id == "2"  # now-playing track unchanged
    assert _ids(app._play_queue) == ["3"]


def test_clear_history_noop_when_already_at_head(app):
    app._timeline = [_track("0"), _track("1")]
    app._cursor = 0
    before = app.queue_version
    app.clear_history()
    assert app.queue_version == before
