"""Shuffle: with it on, what is queued to play next is in random order, so
Up Next (and gapless, which lines up its first track) show the real order.

Covered: starting from a list (the chosen track first), turning it on over a
queue, adding to the end, Play next staying explicit, history untouched, off
leaving the queue as it is, the flag/setting, the tag and Qobuz Connect."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from qobit.qobuz.models import Track


def _t(name: str) -> Track:
    return Track(id=name, title=name, artist="A", album="B", album_id="1", duration=200)


def _ids(tracks: list[Track]) -> str:
    return "".join(t.id for t in tracks)


@pytest.fixture
def shuffled(ui_app, monkeypatch):
    """Deterministic: random.shuffle reverses, randint picks the end less one."""
    import qobit.ui.play_context as pc

    monkeypatch.setattr(pc.random, "shuffle", lambda x: x.reverse())
    monkeypatch.setattr(pc.random, "randint", lambda a, b: max(a, b - 1))
    started: list[str] = []

    async def do_play(track, start=0.0):
        started.append(track.id)

    monkeypatch.setattr(ui_app, "_do_play", do_play)
    return SimpleNamespace(app=ui_app, started=started)


def _run(body):
    asyncio.run(body())


def test_starting_from_a_list_plays_the_chosen_track_first_rest_shuffled(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)):
            app = shuffled.app
            app.shuffle = True
            await app._ctx.load(_t("A"), [_t("B"), _t("C"), _t("D")], 0.0)
            assert _ids(app._timeline) == "ADCB" and app._cursor == 0
            assert shuffled.started == ["A"]

    _run(body)


def test_off_keeps_the_list_order(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)):
            app = shuffled.app
            await app._ctx.load(_t("A"), [_t("B"), _t("C"), _t("D")], 0.0)
            assert _ids(app._timeline) == "ABCD"

    _run(body)


def test_turning_it_on_shuffles_up_next_not_the_past(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)):
            app = shuffled.app
            app._timeline = [_t(c) for c in "PQABCD"]
            app._cursor = 2  # A is playing
            before = app.queue_version
            app.action_toggle_shuffle()
            assert app.shuffle is True
            assert _ids(app._timeline) == "PQADCB"  # past and now-playing untouched
            assert app.queue_version > before

    _run(body)


def test_turning_it_off_leaves_the_queue_as_it_is(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)):
            app = shuffled.app
            app._timeline = [_t(c) for c in "ABCD"]
            app._cursor = 0
            app.action_toggle_shuffle()
            order = _ids(app._timeline)
            app.action_toggle_shuffle()
            assert app.shuffle is False and _ids(app._timeline) == order

    _run(body)


def test_added_to_the_end_lands_somewhere_in_up_next(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)):
            app = shuffled.app
            app._timeline = [_t(c) for c in "ABC"]
            app._cursor = 0
            app.shuffle = True
            assert app.queue_last(_t("X"))
            assert _ids(app._timeline) == "ABXC"  # not at the end (stubbed pick)
            app.shuffle = False
            app.queue_last(_t("Y"))
            assert _ids(app._timeline) == "ABXCY"

    _run(body)


def test_play_next_stays_explicit(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)):
            app = shuffled.app
            app._timeline = [_t(c) for c in "ABC"]
            app._cursor = 0
            app.shuffle = True
            app.queue_next(_t("X"))
            assert _ids(app._timeline) == "AXBC"

    _run(body)


def test_a_single_track_queue_is_left_alone(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)):
            app = shuffled.app
            app._timeline = [_t("A"), _t("B")]
            app._cursor = 0
            before = app.queue_version
            app.action_toggle_shuffle()
            assert _ids(app._timeline) == "AB" and app.queue_version == before

    _run(body)


def test_the_tag_follows_the_setting_and_hides_under_connect(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)) as pilot:
            app = shuffled.app
            bar = app.screen_stack[0].query_one("TransportBar")
            assert "SHUFFLE" not in bar.border_title
            app.action_toggle_shuffle()
            await pilot.pause(0.1)
            assert "SHUFFLE" in bar.border_title
            app.connect_state = "active"
            await pilot.pause(0.1)
            assert "SHUFFLE" not in bar.border_title

    _run(body)


def test_under_connect_the_key_says_so_and_changes_nothing(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)):
            app = shuffled.app
            app._connect = SimpleNamespace(active=True, session=SimpleNamespace(stop=lambda: None))
            assert app.check_action("toggle_shuffle", ()) is False
            app.action_toggle_shuffle()
            assert app.shuffle is False

    _run(body)


def test_the_flag_and_the_saved_setting():
    from qobit import config
    from qobit.ui.app import QobitApp

    assert config.get_shuffle() is False
    config.set_shuffle(True)
    assert QobitApp(connect=False).shuffle is True
    assert QobitApp(connect=False, shuffle=False).shuffle is False  # the flag wins
    config.set_shuffle(False)
    assert QobitApp(connect=False, shuffle=True).shuffle is True


def test_the_footer_follows_connect(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)) as pilot:
            app = shuffled.app
            await pilot.pause(0.2)
            calls: list[int] = []
            real = app.refresh_bindings
            app.refresh_bindings = lambda *a, **k: (calls.append(1), real(*a, **k))[1]  # type: ignore[method-assign]
            app.connect_state = "active"
            assert calls  # the footer is asked again when Connect takes over

    _run(body)


def test_a_capital_s_typed_in_the_search_box_is_text_not_shuffle(shuffled):
    async def body():
        async with shuffled.app.run_test(size=(120, 30)) as pilot:
            app = shuffled.app
            app.action_switch_tab("search")
            await pilot.pause(0.3)
            app.screen_stack[0].query_one("#search-input").focus()
            await pilot.pause(0.1)
            from textual.widgets import Input

            assert isinstance(app.focused, Input)  # the search box has the focus
            await pilot.press("S", "o", "S")
            await pilot.pause(0.2)
            assert app.shuffle is False and app.focused.value == "SoS"

    _run(body)
