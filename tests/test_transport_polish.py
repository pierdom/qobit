"""The transport's album line (with the year), the progress bar's track, and
the wordmark's colour cycle."""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace

import pytest
from textual.app import App
from textual.color import Color

from qobit.qobuz.models import Album, Track
from qobit.ui.nav import (
    WORDMARK_TEXT,
    WORDMARK_UNITS,
    Wordmark,
    wordmark_colours,
    wordmark_palette,
)
from qobit.ui.themes import BAR_END, BAR_START, QOBIT_THEMES, qobit_variable_defaults
from qobit.ui.widgets.transport import _TRACK, TIP_TINT, _progress_bar, album_line


def _track(**kw) -> Track:
    base = {
        "id": "1",
        "title": "So What",
        "artist": "Miles Davis",
        "album": "Kind of Blue",
        "album_id": "a1",
        "duration": 545,
    }
    return Track(**(base | kw))


# ── year ──


def test_track_from_api_reads_album_year():
    t = Track.from_api(
        {
            "id": 1,
            "title": "So What",
            "performer": {"name": "Miles Davis"},
            "album": {"id": "a1", "title": "Kind of Blue", "release_date_original": "1959-08-17"},
        }
    )
    assert t.album_year == 1959


@pytest.mark.parametrize("release", [None, "", "19", "n/a"])
def test_track_without_a_usable_date_has_no_year(release):
    t = Track.from_api({"id": 1, "album": {"release_date_original": release}})
    assert t.album_year is None


def test_album_backfills_year_onto_its_tracks():
    album = Album.from_api(
        {
            "id": "a1",
            "title": "Kind of Blue",
            "release_date_original": "1959-08-17",
            "tracks": {"items": [{"id": 1, "title": "So What"}]},
        }
    )
    assert [t.album_year for t in album.tracks] == [1959]


def test_saved_track_without_year_still_loads():
    """Queues and mixes saved before album_year existed."""
    raw = dataclasses.asdict(_track())
    del raw["album_year"]
    assert Track(**raw).album_year is None


def test_album_line():
    assert album_line(_track(album_year=1959)) == "Kind of Blue (1959)"
    assert album_line(_track()) == "Kind of Blue"
    assert album_line(_track(album="", album_year=1959)) == ""


# ── progress bar ──


def _app_with(variables: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(theme_variables=variables)


_BAR_VARS = {"accent": "#00ffff", BAR_START: "#00ffff", BAR_END: "#ff00ff"}


def test_bar_track_is_a_shade():
    bar = _progress_bar(_app_with(_BAR_VARS), 10, 0.0, 100.0)
    assert bar.plain == _TRACK * 10 == "░" * 10


def _tip_style(bar):
    # 10 cells, 45%: 4 full cells, then the half-cell tip at index 4.
    assert bar.plain == "████▌" + _TRACK * 5
    return next(s.style for s in bar.spans if s.start <= 4 < s.end)


def test_bar_tip_is_tinted_like_the_shade():
    bar = _progress_bar(_app_with(_BAR_VARS | {"background": "#000000"}), 10, 45.0, 100.0)
    style = str(_tip_style(bar))
    tint = Color.parse("#000000").blend(Color.parse("#00ffff"), TIP_TINT).hex
    assert style.endswith(f"on {tint}")


@pytest.mark.parametrize(
    "extra",
    [
        {"background": "#000000", "_transparent": True},  # terminal's colour: unknown
        {"background": "ansi_default"},  # ANSI theme: no blending
        {},  # no background known at all
    ],
)
def test_bar_tip_untinted_where_the_background_is_unknown(extra):
    transparent = extra.pop("_transparent", False)
    app = _app_with(_BAR_VARS | extra)
    app._transparent = transparent
    style = str(_tip_style(_progress_bar(app, 10, 45.0, 100.0)))
    assert " on " not in style


def test_bar_full_and_unknown_duration():
    assert _progress_bar(_app_with(_BAR_VARS), 6, 100.0, 100.0).plain == "█" * 6
    assert _progress_bar(_app_with(_BAR_VARS), 6, 5.0, 0.0).plain == _TRACK * 6


# ── wordmark ──


def _theme_app(name: str) -> App:
    app = App()
    for theme in QOBIT_THEMES:
        app.register_theme(theme)
    app.theme = name
    theme = app.available_themes[name]
    app_vars = {**app.get_css_variables(), **qobit_variable_defaults(theme), **theme.variables}
    return _app_with(app_vars)


def test_palette_uses_the_bar_gradient_when_it_has_one():
    start, end = wordmark_palette(_theme_app("qobit-cyberdeck"))
    assert start != end


def test_palette_falls_back_to_accent_and_hires_on_one_colour_bars():
    app = _theme_app("ayu-dark")
    v = app.theme_variables
    assert v.get(BAR_START, v["accent"]) == v.get(BAR_END, v["accent"])
    palette = wordmark_palette(app)
    assert palette is not None
    assert palette[0] != palette[1]


def test_palette_is_none_for_ansi_themes():
    assert wordmark_palette(_theme_app("ansi-dark")) is None


def test_colours_loop_seamlessly():
    a, b = Color.parse("#00ffff"), Color.parse("#ff00ff")
    n = len(WORDMARK_UNITS)
    assert wordmark_colours(a, b, 0.0, n) == wordmark_colours(a, b, 1.0, n)
    # At phase 0 the first letter is the start colour; half a loop on, the end.
    assert wordmark_colours(a, b, 0.0, n)[0] == a.hex
    assert wordmark_colours(a, b, 0.5, n)[0] == b.hex
    # Small phase steps give small colour steps: a slide, not a jump.
    for i in range(100):
        now = wordmark_colours(a, b, i / 100, n)[0]
        nxt = wordmark_colours(a, b, (i + 1) / 100, n)[0]
        ra, rb = Color.parse(now).rgb, Color.parse(nxt).rgb
        assert max(abs(x - y) for x, y in zip(ra, rb, strict=True)) <= 12


def _hold_play_state(app, monkeypatch) -> None:
    """No playback poll: every 0.5 s it sees no mpv in a test and turns
    is_playing off, racing the assertions (it failed CI). The tests set
    the play state themselves."""
    monkeypatch.setattr(app, "_poll_player", lambda: None)


def test_wordmark_cycles_only_while_playing(ui_app, monkeypatch):
    _hold_play_state(ui_app, monkeypatch)
    asyncio.run(_wordmark_cycles(ui_app))


async def _wordmark_cycles(ui_app):
    async with ui_app.run_test(size=(160, 40)) as pilot:
        mark = ui_app.screen.query_one(Wordmark)
        assert mark._timer._active.is_set() is False
        static = mark.render().spans  # Content == compares the text only

        ui_app.now_playing = _track()
        ui_app.is_playing = True
        await pilot.pause()
        assert mark._timer._active.is_set()
        assert mark.render().spans != static

        ui_app.is_paused = True
        await pilot.pause()
        assert mark._timer._active.is_set() is False
        assert mark.render().spans == static


def test_the_mark_glyphs_share_one_colour(ui_app, monkeypatch):
    """◢◤ is one shape: cycled per character, the gradient split it."""
    _hold_play_state(ui_app, monkeypatch)
    asyncio.run(_mark_one_colour(ui_app))


async def _mark_one_colour(ui_app):
    async with ui_app.run_test(size=(160, 40)) as pilot:
        mark = ui_app.screen.query_one(Wordmark)
        ui_app.now_playing = _track()
        ui_app.is_playing = True
        await pilot.pause()
        for _ in range(5):
            content = mark.render()
            assert content.plain == WORDMARK_TEXT
            styles = {str(s.style) for s in content.spans if s.start < 2}
            assert len(styles) == 1
            covered = [s for s in content.spans if s.start == 0]
            assert covered and covered[0].end == 2
            await pilot.pause(0.15)
