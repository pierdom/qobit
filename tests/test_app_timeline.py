"""QobitApp's timeline+cursor play context (CLAUDE.md: "Timeline + cursor").

This is the trickiest piece of app.py: history and up_next are derived
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
from test_stream_resolver import RESTRICTED, FakeClient

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


# ── history / up_next slices ───────────────────────────────────────────


def test_history_and_queue_empty_when_nothing_loaded(app):
    assert app._cursor == -1
    assert app.history == []
    assert app.up_next == []


def test_history_is_everything_behind_cursor_queue_is_everything_ahead(app):
    tracks = [_track(str(i)) for i in range(5)]
    app._timeline = tracks
    app._cursor = 2
    assert _ids(app.history) == ["0", "1"]
    assert _ids(app.up_next) == ["3", "4"]
    # the now-playing track itself is in neither slice
    now_playing_id = tracks[app._cursor].id
    assert now_playing_id not in _ids(app.history)
    assert now_playing_id not in _ids(app.up_next)


def test_history_empty_at_start_of_timeline(app):
    tracks = [_track(str(i)) for i in range(3)]
    app._timeline = tracks
    app._cursor = 0
    assert app.history == []
    assert _ids(app.up_next) == ["1", "2"]


def test_play_queue_empty_at_end_of_timeline(app):
    tracks = [_track(str(i)) for i in range(3)]
    app._timeline = tracks
    app._cursor = 2
    assert _ids(app.history) == ["0", "1"]
    assert app.up_next == []


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
    assert len(app.history) == 5
    assert _ids(app.history) == ["4", "5", "6", "7", "8"]


def test_trim_history_preserves_up_next_untouched(app, monkeypatch):
    monkeypatch.setattr(app_module, "_HISTORY_MAX", 2)
    tracks = [_track(str(i)) for i in range(6)]
    app._timeline = tracks
    app._cursor = 4  # history=[0,1,2,3], now=4, up next=[5]
    app._trim_history()
    assert _ids(app.up_next) == ["5"]


# ── queue_next / queue_last ──────────────────────────────────────────────────


def test_queue_next_inserts_immediately_after_now_playing(app):
    tracks = [_track(str(i)) for i in range(3)]
    app._timeline = tracks
    app._cursor = 1  # now playing "1", up next ["2"]
    new_track = _track("new")

    app.queue_next(new_track)

    assert _ids(app.up_next) == ["new", "2"]


def test_queue_last_appends_to_the_tail_of_up_next(app):
    tracks = [_track(str(i)) for i in range(3)]
    app._timeline = tracks
    app._cursor = 1
    new_track = _track("new")

    app.queue_last(new_track)

    assert _ids(app.up_next) == ["2", "new"]


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
    assert app.up_next == [dup_a]  # dup_a (not dup_b) survives, by identity


def test_remove_from_queue_falls_back_to_id_match_if_identity_lost(app):
    """If the exact instance isn't found (e.g. the row was rebuilt from the
    same track id), falls back to removing the first id match ahead of the
    cursor rather than silently doing nothing."""
    app._timeline = [_track("0"), _track("dup")]
    app._cursor = 0
    lookalike = _track("dup")  # a different instance, same id

    removed = app.remove_from_queue(lookalike)

    assert removed is True
    assert app.up_next == []


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

    assert _ids(app.history) == ["0"]
    assert app.up_next == []
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
    assert _ids(app.up_next) == ["3"]


def test_clear_history_noop_when_already_at_head(app):
    app._timeline = [_track("0"), _track("1")]
    app._cursor = 0
    before = app.queue_version
    app.clear_history()
    assert app.queue_version == before


class _FakePlayer:
    running = False

    def __init__(self):
        self.played = []

    def stop(self):
        self.running = False

    def play(self, url, start=0.0):
        self.played.append(url)
        self.running = True


def _all_restricted(app, monkeypatch) -> _FakePlayer:
    app.streams.client = FakeClient(
        {"FLAC_24_192": RESTRICTED, "FLAC_24_96": RESTRICTED, "FLAC_CD": RESTRICTED}
    )
    player = _FakePlayer()
    monkeypatch.setattr(app, "_player", player)
    return player


def test_play_stream_reports_a_restricted_track_unavailable(app, monkeypatch):
    """The path the Next key took: no crash, "unavailable", nothing to mpv."""
    player = _all_restricted(app, monkeypatch)
    assert asyncio.run(app._play_stream(_track("42"))) == "unavailable"
    assert player.played == []


def test_a_restricted_track_is_skipped_like_a_preview(app, monkeypatch):
    """Next onto a restricted track left the old song playing under the new
    cursor ("No stream available"); it's skipped instead."""
    _all_restricted(app, monkeypatch)
    advanced = []
    monkeypatch.setattr(app, "_advance_queue", lambda: advanced.append(True))
    flashes = []
    monkeypatch.setattr(app, "_flash_status", flashes.append)
    app._timeline = [_track("1"), _track("42"), _track("3")]
    app._cursor = 1
    asyncio.run(app._do_play(app._timeline[1]))
    assert advanced == [True]
    assert flashes == ["Not available on Qobuz: Track 42"]


def test_a_restricted_last_track_stops(app, monkeypatch):
    player = _all_restricted(app, monkeypatch)
    player.running = True  # the previous song
    monkeypatch.setattr(app, "_advance_queue", lambda: pytest.fail("nothing to skip to"))
    monkeypatch.setattr(app, "_flash_status", lambda _m: None)
    app._timeline = [_track("1"), _track("42")]
    app._cursor = 1
    asyncio.run(app._do_play(app._timeline[1]))
    assert player.running is False


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
    assert _ids(restored.history) == ["a"] and _ids(restored.up_next) == ["c", "d"]
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


def test_slow_stream_resolve_does_not_override_a_newer_pick(app, monkeypatch):
    """Pick A then B quickly; A's URL arrives last. It must not stop B and play
    A (the cursor would say B while A plays)."""
    from qobit.qobuz.models import StreamUrl

    delays = {"A": 0.05, "B": 0.0}

    async def resolve(track_id):
        await asyncio.sleep(delays[track_id])
        return StreamUrl(url=f"url-{track_id}", format_id=27, sampling_rate=96000, bit_depth=24)

    player = _FakePlayer()
    monkeypatch.setattr(app.streams, "resolve", resolve)
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
    monkeypatch.setattr(app._client, "user_auth_token", "tok")  # logged in: it fetches
    assert asyncio.run(app.library.ensure_track_ids()) == set()
    assert asyncio.run(app.library.ensure_track_ids()) == {"7"}


# ── "Play radio" from the i menu ────────────────────────────────────────────


def _radio_calls(app, monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(
        app, "play_track", lambda t, start=0.0, **_: calls.append(("play", t.id, start))
    )
    monkeypatch.setattr(app, "_start_radio", lambda t: calls.append(("radio", t.id)))
    return calls


@pytest.mark.parametrize("paused", [False, True])
def test_play_radio_on_the_current_track_keeps_it_playing(app, monkeypatch, paused):
    """It restarted the song: only Up Next should become the station."""
    calls = _radio_calls(app, monkeypatch)
    app.now_playing = _track("7")
    app.is_playing = True
    app.is_paused = paused
    app._on_track_menu("radio", _track("7"))
    assert calls == [("radio", "7")]


def test_play_radio_on_another_track_plays_it_first(app, monkeypatch):
    calls = _radio_calls(app, monkeypatch)
    app.now_playing = _track("7")
    app.is_playing = True
    app._on_track_menu("radio", _track("9"))
    assert calls == [("play", "9", 0.0), ("radio", "9")]


def test_play_radio_on_the_restored_track_resumes_it(app, monkeypatch):
    """Restored at startup, shown paused but not in mpv yet: it starts where
    it was left, not from 0:00."""
    calls = _radio_calls(app, monkeypatch)
    app.now_playing = _track("7")
    app.is_paused = True  # what the restore sets; is_playing stays False
    app._pending_resume = (app.now_playing, 83.0)
    app._on_track_menu("radio", _track("7"))
    assert calls == [("play", "7", 83.0), ("radio", "7")]
