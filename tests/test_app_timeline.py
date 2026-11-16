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

import asyncio

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


# ── _resolve_stream (quality fallback + preview detection) ──────────────────


class _FakeClient:
    """Answers get_streaming_url from a per-quality script; records calls."""

    def __init__(self, script: dict):
        self.script = script
        self.calls: list[str] = []

    async def get_streaming_url(self, track_id: str, quality: str) -> dict:
        self.calls.append(quality)
        answer = self.script[quality]
        if isinstance(answer, Exception):
            raise answer
        return answer


_FLAC = {"url": "u", "format_id": 7, "sampling_rate": 96, "bit_depth": 24}
_PREVIEW = {"url": "p", "format_id": 5, "sample": True, "sampling_rate": 44.1}


def test_resolve_stream_returns_first_working_quality_and_caches_it(app):
    from qobit.qobuz.client import QobuzError

    app._client = _FakeClient({"FLAC_24_192": QobuzError("nope"), "FLAC_24_96": _FLAC})
    stream = asyncio.run(app._resolve_stream("42"))
    assert stream is not None and not stream.sample
    assert app._client.calls == ["FLAC_24_192", "FLAC_24_96"]
    assert app._track_quality_cache["42"] == "FLAC_24_96"


def test_resolve_stream_stops_at_a_preview_and_does_not_cache_it(app):
    # Qobuz returns the same preview at every quality — no point walking on.
    app._client = _FakeClient(
        {"FLAC_24_192": _PREVIEW, "FLAC_24_96": _PREVIEW, "FLAC_CD": _PREVIEW}
    )
    stream = asyncio.run(app._resolve_stream("42"))
    assert stream is not None and stream.sample
    assert app._client.calls == ["FLAC_24_192"]
    assert "42" not in app._track_quality_cache


def test_resolve_stream_none_when_every_quality_fails(app):
    from qobit.qobuz.client import QobuzError

    err = QobuzError("nope")
    app._client = _FakeClient({"FLAC_24_192": err, "FLAC_24_96": err, "FLAC_CD": err})
    assert asyncio.run(app._resolve_stream("42")) is None


# What Qobuz sent for a track the account can't stream, not even as a
# preview: no URL, only a restriction code. The KeyError closed the app.
_RESTRICTED = {
    "track_id": 31052309,
    "duration": 219,
    "restrictions": [{"code": "SampleRestrictedByRightHolders"}],
    "sampling_rate": 192,
    "bit_depth": 24,
}


def test_resolve_stream_none_when_every_quality_is_restricted(app):
    app._client = _FakeClient(
        {"FLAC_24_192": _RESTRICTED, "FLAC_24_96": _RESTRICTED, "FLAC_CD": _RESTRICTED}
    )
    assert asyncio.run(app._resolve_stream("42")) is None
    assert app._client.calls == ["FLAC_24_192", "FLAC_24_96", "FLAC_CD"]
    assert "42" not in app._track_quality_cache


def test_resolve_stream_falls_back_past_a_restricted_quality(app):
    app._client = _FakeClient({"FLAC_24_192": _RESTRICTED, "FLAC_24_96": _FLAC})
    stream = asyncio.run(app._resolve_stream("42"))
    assert stream is not None and stream.url == "u"
    assert app._track_quality_cache["42"] == "FLAC_24_96"


def test_play_stream_reports_a_restricted_track_unplayable(app):
    """The whole path the Next key took: no crash, "none" for the caller to
    skip, and nothing handed to mpv."""
    app._client = _FakeClient(
        {"FLAC_24_192": _RESTRICTED, "FLAC_24_96": _RESTRICTED, "FLAC_CD": _RESTRICTED}
    )
    played: list = []
    app._player.play = lambda *a, **k: played.append(a)
    track = Track(id="42", title="t", artist="a", album="b", album_id="c", duration=1)
    assert asyncio.run(app._play_stream(track)) == "none"
    assert played == []


def test_resolve_stream_propagates_auth_expired(app):
    from qobit.qobuz.client import AuthExpiredError

    app._client = _FakeClient({"FLAC_24_192": AuthExpiredError("401")})
    with pytest.raises(AuthExpiredError):
        asyncio.run(app._resolve_stream("42"))


# ── queue persistence (queue_state.json + player_state.json) ────────────────


def _fresh_app(monkeypatch):
    monkeypatch.setattr(app_module, "MediaKeys", _NoOpMediaKeys)
    return app_module.QobitApp()


def _save_player(app, track: Track, position: float) -> None:
    app.now_playing = track
    app.playback_pos = position
    app.playback_dur = float(track.duration)
    app._save_player_state()


def test_queue_round_trips_across_restart(app, monkeypatch):
    app._timeline = [_track("a"), _track("b"), _track("c"), _track("d")]
    app._cursor = 1
    app._save_queue_state()
    _save_player(app, app._timeline[1], 42.0)

    restored = _fresh_app(monkeypatch)
    restored._restore_player_state()
    assert _ids(restored._timeline) == ["a", "b", "c", "d"]
    assert restored._cursor == 1
    assert _ids(restored._history) == ["a"] and _ids(restored._play_queue) == ["c", "d"]
    assert restored.now_playing.id == "b" and restored.is_paused
    assert restored._pending_resume == (restored._timeline[1], 42.0)


def test_restore_falls_back_to_last_track_when_files_disagree(app, monkeypatch):
    # Killed between saves: the queue's cursor points at a different track than
    # the one last played. Don't mix them — keep just the last-played track.
    app._timeline = [_track("a"), _track("b")]
    app._cursor = 0
    app._save_queue_state()
    _save_player(app, _track("zzz"), 10.0)

    restored = _fresh_app(monkeypatch)
    restored._restore_player_state()
    assert _ids(restored._timeline) == ["zzz"] and restored._cursor == 0


def test_restore_without_any_saved_state_is_empty(monkeypatch):
    restored = _fresh_app(monkeypatch)
    restored._restore_player_state()
    assert restored._timeline == [] and restored._cursor == -1
    assert restored.now_playing is None


def test_corrupt_queue_file_is_ignored(app, monkeypatch):
    from qobit import store

    store.save(app_module._QUEUE_STATE_FILE, {"timeline": [{"bogus": 1}], "cursor": 0})
    _save_player(app, _track("x"), 5.0)
    restored = _fresh_app(monkeypatch)
    restored._restore_player_state()
    assert _ids(restored._timeline) == ["x"]


def test_queue_with_nothing_played_yet_restores_without_now_playing(app, monkeypatch):
    # e.g. a radio station queued before pressing play: cursor -1, no player state.
    app._timeline = [_track("r1"), _track("r2")]
    app._cursor = -1
    app._save_queue_state()
    restored = _fresh_app(monkeypatch)
    restored._restore_player_state()
    assert _ids(restored._timeline) == ["r1", "r2"] and restored._cursor == -1
    assert restored.now_playing is None


# ── overlapping play requests ───────────────────────────────────────────────


class _FakePlayer:
    running = False

    def __init__(self):
        self.played = []

    def stop(self):
        self.running = False

    def play(self, url, start=0.0):
        self.played.append(url)
        self.running = True


def test_slow_stream_resolve_does_not_override_a_newer_pick(app, monkeypatch):
    """Pick A then B quickly; A's URL arrives last. It must not stop B and play
    A (the cursor would say B while A plays)."""
    from qobit.qobuz.models import StreamUrl

    delays = {"A": 0.05, "B": 0.0}

    async def resolve(track_id):
        await asyncio.sleep(delays[track_id])
        return StreamUrl(url=f"url-{track_id}", format_id=27, sampling_rate=96000, bit_depth=24)

    player = _FakePlayer()
    monkeypatch.setattr(app, "_resolve_stream", resolve)
    monkeypatch.setattr(app, "_player", player)
    monkeypatch.setattr(app, "_fetch_now_playing_album", lambda track: None)

    async def run():
        return await asyncio.gather(app._play_stream(_track("A")), app._play_stream(_track("B")))

    results = asyncio.run(run())
    assert results == ["stale", "ok"]
    assert player.played == ["url-B"]
    assert app.now_playing.id == "B"


# ── favourites cache ────────────────────────────────────────────────────────


def test_failed_favourites_fetch_is_retried_not_cached(app, monkeypatch):
    """One failed fetch at startup used to cache an empty set: no hearts for
    the whole session."""
    from qobit.qobuz.client import QobuzError

    calls = []

    async def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise QobuzError("Network error")
        return [{"id": 7, "title": "T"}]

    monkeypatch.setattr(app._client, "get_all_favorite_tracks", fetch)
    assert asyncio.run(app.ensure_favorite_ids()) == set()
    assert asyncio.run(app.ensure_favorite_ids()) == {"7"}
