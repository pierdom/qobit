"""Lyrics in the app: a lookup when the track changes (and a stale answer is
dropped), the line being sung following the position, the early fetch for the
next track, the nudge and "wrong lyrics" keys, the switch, the Playback panel
and the lyrics page, and logout erasing the cache. Runs the real QobitApp
headless with a fake lyrics service (nothing goes to the network)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from album_pages import wait_until

from qobit import config
from qobit.lyrics import Line, Lyrics, Query, Result
from qobit.lyrics import cache as lyrics_cache
from qobit.qobuz.models import Track
from qobit.ui.screens.lyrics import LyricsScreen
from qobit.ui.screens.queue import QueueView
from qobit.ui.widgets.lyrics import LyricsView, wrap


def _track(name: str, isrc: str | None = None) -> Track:
    return Track(id=name, title=name, artist="A", album="B", album_id="1", duration=200, isrc=isrc)


SYNCED = Lyrics(
    [Line("first line", 10.0), Line("second line", 20.0), Line("third line", 30.0)],
    synced=True,
    source="LRCLIB",
)
PLAIN = Lyrics([Line(f"plain {i}") for i in range(60)], synced=False, source="LRCLIB")


class FakeService:
    def __init__(self) -> None:
        self.answers: dict[str, Result] = {}
        self.queries: list[Query] = []
        self.offsets: list[tuple[str, float]] = []
        self.rejected: list[str] = []
        self.gate: asyncio.Event | None = None  # holds every lookup until set

    async def lookup(self, query: Query) -> Result:
        self.queries.append(query)
        if self.gate is not None:
            await self.gate.wait()
        return self.answers.get(query.title, Result("none"))

    async def set_offset(self, query: Query, offset: float) -> None:
        self.offsets.append((query.title, offset))

    async def reject(self, query: Query) -> None:
        self.rejected.append(query.title)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def lyr(ui_app, monkeypatch):
    service = FakeService()
    # The poll sees no mpv: it would end the track, advance and reset the position.
    monkeypatch.setattr(ui_app, "_poll_player", lambda: None)
    ui_app._lyrics = service
    ui_app._maybe_prefetch = lambda: None  # gapless's early fetch is tested in test_gapless
    service.answers["A"] = Result("found", SYNCED)
    service.answers["B"] = Result("found", PLAIN)
    service.answers["I"] = Result("instrumental", Lyrics(instrumental=True))

    def play(track: Track, queue: list[Track] | None = None, pos: float = 0.0) -> None:
        ui_app._timeline = [track, *(queue or [])]
        ui_app._cursor = 0
        ui_app.is_playing, ui_app.is_paused = True, False
        ui_app.playback_dur, ui_app.playback_pos = float(track.duration), pos
        ui_app.now_playing = track

    return SimpleNamespace(app=ui_app, service=service, play=play)


def _run(body):
    asyncio.run(body())


async def press_until(pilot, key: str, condition, tries: int = 3) -> None:
    """Press ``key`` until ``condition()`` holds. On CI runners a key pressed
    soon after the app starts is now and then not acted on (never seen
    locally, whatever the load), so it is sent again when nothing happened;
    the conditions here only hold after the key has been taken."""
    for _ in range(tries):
        await pilot.press(key)
        for _ in range(40):  # 2 s
            if condition():
                return
            await pilot.pause(0.05)
    await wait_until(pilot, condition)  # fails with the usual message


def _screen_text(app) -> str:
    return "\n".join(s.text for s in app.screen._compositor.render_strips())


# ── lookup ───────────────────────────────────────────────────────────────────


def test_a_new_track_is_looked_up_with_its_isrc_and_shown(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            lyr.play(_track("A", isrc="GB123"))
            assert lyr.app.lyrics_status == "loading"
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "found")
            assert lyr.app.lyrics is SYNCED
            q = lyr.service.queries[0]
            assert (q.title, q.artist, q.album, q.duration, q.isrc) == ("A", "A", "B", 200, "GB123")

    _run(body)


def test_instrumental_none_and_failed_statuses(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            lyr.play(_track("I"))
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "instrumental")
            lyr.play(_track("nothing"))
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "none")
            lyr.service.answers["down"] = Result("failed")
            lyr.play(_track("down"))
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "failed")
            assert lyr.app.lyrics is None

    _run(body)


def test_a_late_answer_for_the_previous_track_is_dropped(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            lyr.service.gate = asyncio.Event()
            lyr.play(_track("A"))
            await pilot.pause(0.1)
            lyr.service.gate = None
            lyr.play(_track("B"))  # a new track while A's lookup is in flight
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "found")
            assert lyr.app.lyrics is PLAIN
            await pilot.pause(0.2)
            assert lyr.app.lyrics is PLAIN  # A's answer never landed

    _run(body)


def test_an_answer_arriving_after_stop_or_switch_off_is_dropped(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            for stop in (lambda: setattr(app, "now_playing", None), app.action_toggle_lyrics):
                lyr.service.gate = asyncio.Event()
                lyr.play(_track("A"))
                await pilot.pause(0.1)  # the lookup is in flight
                stop()  # no new track to cancel it: only the check on arrival drops it
                lyr.service.gate.set()
                await pilot.pause(0.2)
                assert app.lyrics is None and app.lyrics_status in ("idle", "off")
                app.lyrics_enabled = True

    _run(body)


def test_stopping_clears_the_lyrics(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            lyr.play(_track("A"))
            await wait_until(pilot, lambda: lyr.app.lyrics is not None)
            lyr.app.now_playing = None
            assert lyr.app.lyrics is None and lyr.app.lyrics_status == "idle"

    _run(body)


# ── the line being sung ──────────────────────────────────────────────────────


def test_the_current_line_follows_the_position_and_changes_only_with_the_line(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            lyr.play(_track("A"))
            await wait_until(pilot, lambda: app.lyrics is not None)
            changes: list[int] = []
            app.watch(app, "lyrics_line", lambda v: changes.append(v), init=False)
            seen = []
            for pos in (0.0, 5.0, 9.8, 10.5, 15.0, 19.9, 25.0, 31.0, 150.0):
                app.playback_pos = pos
                seen.append(app.lyrics_line)
            # 9.8 is already line 0: the poll's lag is covered by a lead
            assert seen == [-1, -1, 0, 0, 0, 1, 1, 2, 2]
            assert changes == [0, 1, 2]  # one change per line, not per tick

    _run(body)


def test_a_seek_back_goes_back_a_line(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            lyr.play(_track("A"), pos=31.0)
            await wait_until(pilot, lambda: lyr.app.lyrics is not None)
            lyr.app.playback_pos = 31.5
            assert lyr.app.lyrics_line == 2
            lyr.app.playback_pos = 12.0
            assert lyr.app.lyrics_line == 0

    _run(body)


# ── the early fetch ──────────────────────────────────────────────────────────


def test_the_next_tracks_lyrics_are_fetched_once_near_the_end(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            lyr.play(_track("A"), queue=[_track("B")])
            await wait_until(pilot, lambda: app.lyrics is not None)
            app.playback_pos = 100.0  # 100 s left: too early
            await pilot.pause(0.2)
            assert [q.title for q in lyr.service.queries] == ["A"]
            for pos in (160.0, 161.0, 170.0):  # within 45 s of the end
                app.playback_pos = pos
            await wait_until(pilot, lambda: [q.title for q in lyr.service.queries] == ["A", "B"])
            await pilot.pause(0.2)
            assert [q.title for q in lyr.service.queries] == ["A", "B"]  # once

    _run(body)


@pytest.mark.parametrize("why", ["paused", "off", "connect"])
def test_no_early_fetch_when_paused_off_or_under_connect(lyr, why):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            lyr.play(_track("A"), queue=[_track("B")])
            await wait_until(pilot, lambda: app.lyrics is not None)
            if why == "paused":
                app.is_paused = True
            elif why == "off":
                app.lyrics_enabled = False
            else:
                app._connect = SimpleNamespace(
                    active=True, session=SimpleNamespace(stop=lambda: None)
                )
            app.playback_pos = 180.0
            await pilot.pause(0.3)
            assert [q.title for q in lyr.service.queries] == ["A"]

    _run(body)


# ── the switch ───────────────────────────────────────────────────────────────


def test_off_looks_nothing_up_and_on_looks_the_playing_track_up(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            lyr.play(_track("A"))
            await wait_until(pilot, lambda: app.lyrics is not None)
            app.action_toggle_lyrics()
            assert app.lyrics_enabled is False and app.lyrics is None and app.lyrics_status == "off"
            assert config.get_lyrics() is False
            lyr.play(_track("B"))
            await pilot.pause(0.2)
            assert [q.title for q in lyr.service.queries] == ["A"]  # nothing asked while off
            app.action_toggle_lyrics()
            await wait_until(pilot, lambda: app.lyrics_status == "found")
            assert app.lyrics is PLAIN and config.get_lyrics() is True

    _run(body)


def test_the_flag_and_the_saved_setting():
    from qobit.ui.app import QobitApp

    assert config.get_lyrics() is True
    config.set_lyrics(False)
    assert QobitApp(connect=False).lyrics_enabled is False
    assert QobitApp(connect=False, lyrics=True).lyrics_enabled is True  # the flag wins
    config.set_lyrics(True)
    assert QobitApp(connect=False, lyrics=False).lyrics_enabled is False


# ── nudge and reject ─────────────────────────────────────────────────────────


def test_the_nudge_shifts_the_lines_saves_the_offset_and_shows_it(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            lyr.play(_track("A"), pos=20.0)
            await wait_until(pilot, lambda: app.lyrics is not None)
            app.playback_pos = 20.1
            assert app.lyrics_line == 1
            app.action_lyrics_later()  # the lyrics come 0.5 s later: line 2 not yet
            app.playback_pos = 20.2
            assert app.lyrics_offset == 0.5
            await wait_until(pilot, lambda: lyr.service.offsets == [("A", 0.5)])
            app.playback_pos = 20.0
            app.action_lyrics_earlier()
            app.action_lyrics_earlier()
            assert app.lyrics_offset == -0.5
            assert "-0.5 s" in lyr.app.screen_stack[0].query_one(LyricsView).border_subtitle

    _run(body)


def test_the_stored_offset_applies_from_the_start(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            lyr.service.answers["A"] = Result("found", SYNCED, offset=2.0)
            lyr.play(_track("A"), pos=21.0)
            await wait_until(pilot, lambda: app.lyrics is not None)
            app.playback_pos = 21.0
            assert app.lyrics_line == 0  # line 1 is at 20 + 2

    _run(body)


def test_reject_removes_the_lyrics_and_tells_the_service(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            lyr.play(_track("A"))
            await wait_until(pilot, lambda: app.lyrics is not None)
            app.action_lyrics_reject()
            assert app.lyrics is None and app.lyrics_status == "none"
            await wait_until(pilot, lambda: lyr.service.rejected == ["A"])
            app.action_lyrics_reject()  # nothing left to reject
            assert lyr.service.rejected == ["A"]

    _run(body)


def test_the_keys_are_offered_only_while_timed_lyrics_are_on_screen(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            lyr.play(_track("A"))
            await wait_until(pilot, lambda: app.lyrics is not None)
            keys = ("lyrics_earlier", "lyrics_later", "lyrics_reject")
            assert not any(app.check_action(k, ()) for k in keys)  # the hero is showing
            app.action_lyrics()  # the panel
            await pilot.pause(0.1)
            assert all(app.check_action(k, ()) for k in keys)
            lyr.play(_track("B"))  # plain text: nothing to shift
            await wait_until(pilot, lambda: app.lyrics is PLAIN)
            assert not app.check_action("lyrics_later", ()) and app.check_action(
                "lyrics_reject", ()
            )

    _run(body)


# ── the panel and the page ───────────────────────────────────────────────────


def test_l_swaps_the_playback_panel_between_hero_and_lyrics_and_is_remembered(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            view = app.screen_stack[0].query_one(QueueView)
            hero, panel = view.query_one("#now-hero"), view.query_one("#lyrics-panel")
            await pilot.pause(0.2)
            assert hero.display and not panel.display
            await press_until(
                pilot,
                "l",
                lambda: panel.display and not hero.display and config.get_lyrics_panel() is True,
            )
            await press_until(
                pilot,
                "l",
                lambda: hero.display and not panel.display and config.get_lyrics_panel() is False,
            )

    _run(body)


def test_l_on_a_narrow_terminal_or_another_tab_opens_the_page_and_escape_closes_it(lyr):
    async def body():
        async with lyr.app.run_test(size=(90, 30)) as pilot:  # narrower than the panel needs
            app = lyr.app
            await pilot.pause(0.2)
            await press_until(pilot, "l", lambda: isinstance(app.screen, LyricsScreen))
            assert app.lyrics_panel is False
            await pilot.press("escape")
            await wait_until(pilot, lambda: not isinstance(app.screen, LyricsScreen))
            app.action_switch_tab("tracks")
            await wait_until(pilot, lambda: app.base_tab() == "tracks")
            await press_until(pilot, "l", lambda: isinstance(app.screen, LyricsScreen))
            await press_until(pilot, "l", lambda: not isinstance(app.screen, LyricsScreen))

    _run(body)


def test_capital_l_opens_the_page_from_anywhere(lyr):
    async def body():
        async with lyr.app.run_test(size=(140, 36)) as pilot:
            app = lyr.app
            await pilot.pause(0.2)
            await press_until(pilot, "L", lambda: isinstance(app.screen, LyricsScreen))
            await press_until(pilot, "L", lambda: not isinstance(app.screen, LyricsScreen))

    _run(body)


def test_the_page_draws_the_sung_line_in_the_middle_and_follows_it(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 40)) as pilot:
            app = lyr.app
            lyr.play(_track("A"), pos=21.0)
            await wait_until(pilot, lambda: app.lyrics is not None)
            await pilot.press("L")
            await pilot.pause(0.4)
            text = _screen_text(app)
            assert "first line" in text and "second line" in text and "third line" in text
            assert "LRCLIB" in text and "LYRICS" in text
            app.playback_pos = 31.0
            await pilot.pause(0.2)
            assert app.lyrics_line == 2
            assert "third line" in _screen_text(app)

    _run(body)


def test_the_sung_line_is_drawn_in_a_different_style(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 40)) as pilot:
            app = lyr.app
            lyr.play(_track("A"), pos=21.0)
            await wait_until(pilot, lambda: app.lyrics is not None)
            app.playback_pos = 21.0
            await pilot.press("L")
            await pilot.pause(0.4)
            view = app.screen.query_one(LyricsView)
            text = view.render()
            styles = {
                span_text: str(span.style)
                for span in text.spans
                for span_text in [text.plain[span.start : span.end]]
            }
            assert "bold" in styles["second line"]  # the one being sung
            assert styles["first line"] != styles["second line"]  # already sung: muted
            assert "third line" not in styles  # still to come: the plain colour

    _run(body)


@pytest.mark.parametrize(
    ("status", "words"),
    [
        ("none", "No lyrics found"),
        ("instrumental", "Instrumental"),
        ("loading", "Looking for lyrics"),
        ("failed", "Couldn't reach"),
        ("off", "Lyrics are off"),
        ("idle", "Nothing playing"),
    ],
)
def test_the_page_says_quietly_why_there_are_no_lines(lyr, status, words):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            await pilot.pause(0.3)
            await pilot.press("L")
            await pilot.pause(0.3)
            app.lyrics_status = status
            app.lyrics_version += 1
            await pilot.pause(0.2)
            assert words in _screen_text(app)

    _run(body)


def test_untimed_lyrics_scroll_and_do_not_follow(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            app = lyr.app
            lyr.play(_track("B"))
            await wait_until(pilot, lambda: app.lyrics is PLAIN)
            await pilot.pause(0.3)
            await pilot.press("L")
            await pilot.pause(0.4)
            assert "plain 0" in _screen_text(app) and "plain 59" not in _screen_text(app)
            await pilot.press("pagedown", "pagedown")
            await pilot.pause(0.2)
            text = _screen_text(app)
            assert "plain 0 " not in text and "plain 40" in text
            await pilot.press("pageup", "pageup", "pageup")
            await pilot.pause(0.2)
            assert "plain 0" in _screen_text(app)  # clamped at the top

    _run(body)


def test_wrapping_breaks_at_spaces_cuts_long_words_and_counts_wide_characters():
    assert wrap("short", 20) == ["short"]
    assert wrap("one two three four", 9) == ["one two", "three", "four"]
    assert wrap("abcdefghij", 4) == ["abcd", "efgh", "ij"]
    assert all(__import__("rich.cells").cells.cell_len(r) <= 6 for r in wrap("日本語の歌詞です", 6))
    assert wrap("", 10) == [""]


# ── logout and the model ─────────────────────────────────────────────────────


def test_logout_erases_the_cached_lyrics(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            q = Query("T", "A", "", 100)
            lyr.app.request_login = lambda *_a, **_k: None  # the stub can't log in
            lyrics_cache.save(q, Lyrics([Line("x")]))
            assert lyrics_cache.load(q) is not None
            await lyr.app.action_logout()
            await pilot.pause(0.3)
            assert lyrics_cache.load(q) is None

    _run(body)


def test_track_from_api_keeps_the_isrc():
    t = Track.from_api({"id": 1, "title": "x", "isrc": "GBAYE9200070"})
    assert t.isrc == "GBAYE9200070"
    assert Track.from_api({"id": 1, "title": "x"}).isrc is None


# ── the responsive Playback pane ─────────────────────────────────────────────
#
# The right pane shows the hero and the lyrics together when there is room:
# side by side from 140 columns of the view, else one above the other from 26
# rows, else they take turns (`l`). The view is the terminal minus its chrome:
# 2 columns, 10 rows.


def _pane(app):
    view = app.screen_stack[0].query_one(QueueView)
    return view, view.query_one("#now-hero"), view.query_one("#lyrics-panel")


def _classes(view) -> set[str]:
    return {c for c in view.classes if c in ("-narrow", "-side", "-stack", "-lyrics", "-hero-off")}


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((100, 30), {"-narrow"}),
        ((100, 45), {"-narrow"}),  # tall, but too narrow for a right pane at all
        ((120, 30), set()),  # room for one of them: they take turns
        ((120, 35), set()),  # one row short of stacking
        ((120, 36), {"-stack", "-lyrics"}),
        ((141, 30), set()),  # one column short of side by side
        ((141, 36), {"-stack", "-lyrics"}),
        ((142, 24), {"-side", "-lyrics"}),  # wide wins over tall, whatever the height
        ((142, 50), {"-side", "-lyrics"}),
        ((200, 50), {"-side", "-lyrics"}),
    ],
)
def test_the_layout_follows_the_views_size(lyr, size, expected):
    async def body():
        async with lyr.app.run_test(size=size) as pilot:
            await pilot.pause(0.3)
            view, _, _ = _pane(lyr.app)
            assert _classes(view) == expected, (size, view.size)

    _run(body)


def test_side_by_side_puts_the_hero_left_of_the_lyrics_on_the_same_rows(lyr):
    async def body():
        async with lyr.app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(0.3)
            view, hero, panel = _pane(lyr.app)
            lst = view.query_one("#queue-list")
            assert hero.display and panel.display
            assert lst.region.right <= hero.region.x < hero.region.right <= panel.region.x
            assert hero.region.y == panel.region.y and hero.region.height == panel.region.height
            assert hero.region.width >= 38  # what its cover needs
            assert panel.region.width >= 38
            assert panel.region.right <= view.region.right

    _run(body)


def test_stacked_puts_the_hero_above_the_lyrics_in_the_same_columns(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 36)) as pilot:  # the least height that stacks
            await pilot.pause(0.3)
            view, hero, panel = _pane(lyr.app)
            assert hero.display and panel.display
            assert hero.region.x == panel.region.x and hero.region.width == panel.region.width
            assert hero.region.bottom <= panel.region.y
            assert panel.region.height >= 11 and hero.region.height >= 13  # both stay usable
            assert panel.region.bottom <= view.region.bottom
            title = hero.query_one(".hero-title")
            assert title.display and hero.region.contains_region(title.region)  # text kept

    _run(body)


def test_taking_turns_shows_one_and_each_names_the_key_for_the_other(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.3)
            _, hero, panel = _pane(lyr.app)
            assert hero.display and not panel.display and "l" in hero.border_subtitle
            await press_until(pilot, "l", lambda: panel.display and not hero.display)
            assert "now playing" in panel.border_subtitle

    _run(body)


@pytest.mark.parametrize("size", [(120, 36), (160, 40)])
def test_l_opens_the_page_when_both_already_show(lyr, size):
    async def body():
        async with lyr.app.run_test(size=size) as pilot:
            await pilot.pause(0.3)
            _, hero, panel = _pane(lyr.app)
            assert hero.display and panel.display
            assert not hero.border_subtitle and "now playing" not in panel.border_subtitle
            await press_until(pilot, "l", lambda: isinstance(lyr.app.screen, LyricsScreen))
            assert config.get_lyrics_panel() is False  # nothing was swapped or saved

    _run(body)


def test_the_lyrics_keys_work_in_the_stacked_and_side_layouts(lyr):
    async def body():
        for size in ((120, 36), (160, 40)):
            async with lyr.app.run_test(size=size) as pilot:
                await pilot.pause(0.2)
                lyr.play(_track("A"), pos=21.0)
                await wait_until(pilot, lambda: lyr.app.lyrics is not None)
                assert all(lyr.app.check_action(k, ()) for k in ("lyrics_earlier", "lyrics_reject"))
                await pilot.press("full_stop")
                assert lyr.app.lyrics_offset == 0.5
            break  # one app per fixture: the second size runs in its own test below

    _run(body)


def test_resizing_the_terminal_changes_the_layout_live(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.3)
            view, hero, panel = _pane(lyr.app)
            seen = []
            for size in ((120, 40), (160, 40), (120, 30), (100, 40), (160, 30)):
                await pilot.resize_terminal(*size)
                await pilot.pause(0.3)
                shown = [w.display and w.region.width > 0 for w in (hero, panel)]  # on screen
                seen.append((size, _classes(view), *shown))
            assert seen == [
                ((120, 40), {"-stack", "-lyrics"}, True, True),
                ((160, 40), {"-side", "-lyrics"}, True, True),
                ((120, 30), set(), True, False),
                ((100, 40), {"-narrow"}, False, False),
                ((160, 30), {"-side", "-lyrics"}, True, True),
            ]

    _run(body)


def test_a_swapped_lyrics_choice_survives_a_resize_through_the_roomy_layouts(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.3)
            _view, hero, panel = _pane(lyr.app)
            await press_until(pilot, "l", lambda: panel.display and not hero.display)
            await pilot.resize_terminal(160, 40)  # room for both
            await wait_until(pilot, lambda: hero.display and panel.display)
            await pilot.resize_terminal(120, 30)  # back to taking turns: still the lyrics
            await wait_until(pilot, lambda: panel.display and not hero.display)

    _run(body)


@pytest.mark.parametrize("size", [(120, 30), (120, 40), (160, 40)])
def test_with_lyrics_off_the_hero_has_the_pane_to_itself(lyr, size):
    async def body():
        async with lyr.app.run_test(size=size) as pilot:
            await pilot.pause(0.2)
            _view, hero, panel = _pane(lyr.app)
            lyr.app.action_toggle_lyrics()
            await wait_until(pilot, lambda: hero.display and not panel.display)
            assert not hero.border_subtitle
            await pilot.press("l")
            await pilot.pause(0.5)
            assert not isinstance(lyr.app.screen, LyricsScreen)  # says why instead
            lyr.app.action_toggle_lyrics()
            await wait_until(pilot, lambda: panel.display == (size != (120, 30)))

    _run(body)


@pytest.mark.parametrize("width", [100, 110, 120, 130, 140, 200])
def test_the_footer_never_cuts_a_key(lyr, width):
    from textual.widgets._footer import FooterKey

    async def body():
        async with lyr.app.run_test(size=(width, 36)) as pilot:
            lyr.play(_track("A"))
            await pilot.pause(0.5)
            footer = lyr.app.screen_stack[0].query_one("QobitFooter")
            keys = list(footer.query(FooterKey))
            palette = [k for k in keys if k.has_class("-command-palette")]
            assert palette and all(k.display for k in palette)  # the palette hint always stays
            shown = [k for k in keys if k.display and k not in palette]
            assert shown
            for key in shown:
                assert key.region.right <= palette[0].region.x, (width, key.key)
            hidden = [k.key for k in keys if not k.display]
            if hidden:  # the last ones go first, never one in the middle
                assert hidden == [k.key for k in keys if k not in palette][-len(hidden) :]
            if width == 200:
                assert not hidden

    _run(body)


def test_wrapped_rows_are_balanced_so_a_last_word_is_not_alone():
    # greedy at 30 would leave "wind" alone on a second row
    assert wrap("Have turned into whispers on the wind", 30) == [
        "Have turned into",
        "whispers on the wind",
    ]
    rows = wrap("one two three four five six seven eight nine ten eleven twelve", 30)
    assert len(rows) == 3 and max(map(len, rows)) - min(map(len, rows)) <= 5
    assert wrap("fits", 30) == ["fits"]


def _shares(view, hero, panel):
    lst = view.query_one("#queue-list")
    total = lst.region.width + hero.region.width + panel.region.width + 2
    return [round(100 * w.region.width / total) for w in (lst, hero, panel)]


@pytest.mark.parametrize(
    ("width", "shares"),
    [(142, [39, 30, 29]), (180, [33, 36, 30]), (200, [30, 39, 30]), (260, [30, 39, 30])],
)
def test_three_columns_start_at_40_30_30_and_the_hero_takes_the_extra_width(lyr, width, shares):
    async def body():
        async with lyr.app.run_test(size=(width, 40)) as pilot:
            await pilot.pause(0.5)
            view, hero, panel = _pane(lyr.app)
            assert view.has_class("-side")
            for got, want in zip(_shares(view, hero, panel), shares, strict=True):
                assert abs(got - want) <= 1
            assert panel.region.right == view.region.right  # no gap left over
            await pilot.resize_terminal(width + 1, 40)  # odd and even space alike
            await pilot.pause(0.5)
            assert panel.region.right == view.region.right

    _run(body)


def test_two_columns_are_60_40(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            view, _, _ = _pane(lyr.app)
            lst, side = view.query_one("#queue-list"), view.query_one("#side")
            share = lst.region.width / (lst.region.width + side.region.width)
            assert share == pytest.approx(0.6, abs=0.02)

    _run(body)


# ── queue rows ───────────────────────────────────────────────────────────────


def _queue_lines(lyr, size=(120, 36), hires=()):
    """The Playback list's rendered text lines, with a past, a now and an Up Next
    (``hires``: the indexes of the tracks with better than CD quality)."""
    import dataclasses

    out: list[str] = []

    async def body():
        async with lyr.app.run_test(size=size) as pilot:
            ts = [
                dataclasses.replace(
                    _track(f"T{i}"),
                    artist=f"Art{i}",
                    album=f"Alb{i}",
                    duration=d,
                    bit_depth=24 if i in hires else 16,
                    sampling_rate=96 if i in hires else 44.1,
                )
                for i, d in enumerate((200, 247, 294, 341, 388))
            ]
            lyr.play(ts[2], ts[3:])
            lyr.app._timeline, lyr.app._cursor = ts, 2
            lyr.app.queue_version += 1
            await pilot.pause(1.0)
            out.extend(s.text for s in lyr.app.screen._compositor.render_strips())

    _run(body)
    return out


def test_the_duration_is_at_the_right_edge_of_the_title_line(lyr):
    lines = _queue_lines(lyr)
    row = next(ln for ln in lines if "Art3" in ln)
    left = row.index("│") + 1
    right = row.index("│", left)  # the list's right border
    assert row[:right].rstrip().endswith("5:41")
    assert "5:41" not in next(ln for ln in lines if "Alb3" in ln)  # not on the album line
    assert row[left:right].rindex("5:41") + 4 == right - left - 1  # one column of padding


def test_each_section_rule_gives_the_count_and_the_playing_time(lyr):
    lines = _queue_lines(lyr)
    assert any("RECENTLY PLAYED" in ln and "2  ·  7:27" in ln for ln in lines)
    assert any("UP NEXT" in ln and "2  ·  12:09" in ln for ln in lines)
    assert not any("queued" in ln or "played" in ln.replace("RECENTLY PLAYED", "") for ln in lines)


def test_turning_lyrics_off_in_the_three_column_layout_gives_the_cells_back(lyr):
    async def body():
        async with lyr.app.run_test(size=(180, 40)) as pilot:
            await pilot.pause(0.5)
            view, hero, _ = _pane(lyr.app)
            assert view.has_class("-side") and hero.styles.width.unit.name == "CELLS"
            lyr.app.lyrics_enabled = False  # no resize follows
            await pilot.pause(0.5)
            assert not view.has_class("-side")
            assert hero.styles.width is None or hero.styles.width.unit.name != "CELLS"
            side = view.query_one("#side")
            assert hero.region.width == side.region.width  # the hero fills its pane

    _run(body)


# ── a failed lookup is asked again ───────────────────────────────────────────


def test_a_failed_lookup_is_asked_again_and_the_lyrics_arrive(lyr, monkeypatch):
    monkeypatch.setattr("qobit.ui.app._LYRICS_RETRY_SECS", 0.2)

    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            lyr.service.answers["wobbly"] = Result("failed")
            lyr.play(_track("wobbly"))
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "failed")
            lyr.service.answers["wobbly"] = Result("found", SYNCED)  # the network is back
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "found", timeout=3)
            assert lyr.app.lyrics is SYNCED
            asked = len(lyr.service.queries)
            await pilot.pause(0.6)
            assert len(lyr.service.queries) == asked  # and it stops asking

    _run(body)


def test_a_failing_lookup_is_retried_a_few_times_only(lyr, monkeypatch):
    monkeypatch.setattr("qobit.ui.app._LYRICS_RETRY_SECS", 0.1)

    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            lyr.service.answers["down"] = Result("failed")
            lyr.play(_track("down"))
            await pilot.pause(1.2)
            assert [q.title for q in lyr.service.queries].count("down") == 1 + 3
            assert lyr.app.lyrics_status == "failed"

    _run(body)


def test_a_retry_does_not_land_on_another_track(lyr, monkeypatch):
    monkeypatch.setattr("qobit.ui.app._LYRICS_RETRY_SECS", 0.3)

    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            lyr.service.answers["down"] = Result("failed")
            lyr.play(_track("down"))
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "failed")
            lyr.play(_track("A"))
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "found")
            await pilot.pause(0.8)
            assert lyr.app.lyrics is SYNCED
            assert [q.title for q in lyr.service.queries].count("down") == 1

    _run(body)


def test_a_hires_track_shows_it_before_its_length_and_a_cd_one_does_not(lyr):
    lines = _queue_lines(lyr, hires=(3,))
    assert any("Art3" in ln and "HI-RES  5:41" in ln for ln in lines)
    assert not any("HI-RES" in ln and "Art4" in ln for ln in lines)
    row = next(ln for ln in lines if "Art3" in ln)
    right = row.index("│", row.index("│") + 1)  # the list's right border
    assert row[:right].rstrip().endswith("5:41")  # still at the edge


def test_the_queue_border_says_where_you_are(lyr):
    lines = _queue_lines(lyr)
    assert any("3 of 5" in ln for ln in lines)


def test_the_footer_keeps_lyrics_where_it_has_little_room(lyr):
    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.3)
            assert "Lyrics" in _screen_text(lyr.app)

    _run(body)


def test_short_lyrics_sit_in_the_middle_of_the_panel(lyr):
    async def body():
        async with lyr.app.run_test(size=(180, 40)) as pilot:
            lyr.play(_track("A"))
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "found")
            await pilot.pause(0.3)
            _, _, panel = _pane(lyr.app)
            rows = [s.text for s in lyr.app.screen._compositor.render_strips()]
            first = next(n for n, r in enumerate(rows) if "first line" in r)
            assert first - panel.region.y > 3  # not hugging the top border
            below = panel.region.bottom - 1 - max(n for n, r in enumerate(rows) if "line" in r)
            assert abs(below - (first - panel.region.y - 1)) <= 2  # about even above and below

    _run(body)


def test_a_failed_early_fetch_is_asked_for_again_once(lyr, monkeypatch):
    monkeypatch.setattr("qobit.ui.app._LYRICS_RETRY_SECS", 0.2)

    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            lyr.service.answers["next"] = Result("failed")
            lyr.app._lyrics_prefetched = "next"
            lyr.app._prefetch_lyrics(_track("next"))
            await wait_until(pilot, lambda: lyr.app._lyrics_prefetched is None, timeout=3)
            lyr.app._lyrics_prefetched = "next"
            lyr.app._prefetch_lyrics(_track("next"))
            await pilot.pause(0.6)
            assert lyr.app._lyrics_prefetched == "next"  # a second failure is left alone

    _run(body)


def test_a_long_favourite_title_keeps_its_heart_and_its_length(lyr):
    import dataclasses

    out: list[str] = []

    async def body():
        async with lyr.app.run_test(size=(120, 36)) as pilot:
            long = dataclasses.replace(
                _track("L"), artist="Art", title="A very long title " * 6, album="Alb", duration=341
            )
            lyr.play(_track("T"), [long])
            lyr.app._timeline = [_track("T"), long]
            lyr.app._cursor = 0
            lyr.app.queue_version += 1
            lyr.app._fav_ids = {"L"}
            await pilot.pause(1.0)
            out.extend(s.text for s in lyr.app.screen._compositor.render_strips())

    _run(body)
    row = next(ln for ln in out if "Art — A very long" in ln)
    assert "…" in row and "5:41" in row and "♥" in row
    assert row.index("…") < row.index("♥") < row.index("5:41")


def test_a_pending_retry_does_not_outlive_its_lookup(lyr, monkeypatch):
    monkeypatch.setattr("qobit.ui.app._LYRICS_RETRY_SECS", 0.3)

    async def body():
        async with lyr.app.run_test(size=(120, 30)) as pilot:
            lyr.service.answers["down"] = Result("failed")
            lyr.play(_track("down"))
            await wait_until(pilot, lambda: lyr.app.lyrics_status == "failed")
            assert lyr.app._lyrics_retry_timer is not None
            lyr.app.lyrics_enabled = False
            lyr.app._lyrics_track_changed(lyr.app.now_playing)  # what the switch does
            assert lyr.app._lyrics_retry_timer is None
            await pilot.pause(0.6)
            assert [q.title for q in lyr.service.queries].count("down") == 1

    _run(body)
