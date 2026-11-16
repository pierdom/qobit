"""Readable selections and accent text in every theme (qobit's and Textual's
built-ins): themes.qobit_variable_defaults computes them per theme, and this
holds them to WCAG AA (4.5:1). A selected row in ansi-dark once drew accent
text on an accent background."""

from __future__ import annotations

import pytest
from textual.app import App
from textual.color import Color

from qobit.ui.themes import (
    ACCENT_TEXT,
    HEART,
    HIRES,
    NOW,
    QOBIT_THEMES,
    READOUT,
    SELECT,
    SELECT_DIM,
    SELECT_TEXT,
    contrast,
    qobit_variable_defaults,
)


def _themes():
    app = App()
    for theme in QOBIT_THEMES:
        app.register_theme(theme)
    return app, sorted(app.available_themes)


_APP, _NAMES = _themes()


def _resolved(name):
    theme = _APP.available_themes[name]
    _APP.theme = name
    variables = {**qobit_variable_defaults(theme), **theme.variables}
    background = Color.parse(_APP.get_css_variables()["background"])
    return theme, variables, background


def _solid(value, background):
    colour = Color.parse(value)
    return background.blend(colour.with_alpha(1), colour.a)


@pytest.mark.parametrize("name", _NAMES)
def test_selection_text_reads_on_the_selection(name):
    theme, v, background = _resolved(name)
    if theme.ansi:
        # No blending in ANSI: Textual's block-cursor pair, and no tints.
        assert v[SELECT] == theme.variables.get("block-cursor-background", "ansi_white")
        assert v[SELECT_TEXT] == theme.variables.get("block-cursor-foreground", "ansi_black")
        assert v[NOW] == v[SELECT_DIM] == "transparent"
        return
    select = _solid(v[SELECT], background)
    assert contrast(Color.parse(v[SELECT_TEXT]), select) >= 4.5


@pytest.mark.parametrize("name", [n for n in _NAMES if not _APP.available_themes[n].ansi])
@pytest.mark.parametrize("var", [HEART, HIRES, READOUT, ACCENT_TEXT])
def test_coloured_text_reads_on_the_page(name, var):
    _, v, background = _resolved(name)
    assert contrast(_solid(v[var], background), background) >= 4.5


@pytest.mark.parametrize("name", [n for n in _NAMES if not _APP.available_themes[n].ansi])
def test_playing_row_text_reads_on_its_tint(name):
    _, v, background = _resolved(name)
    tint = _solid(v[NOW], background)
    assert contrast(_solid(v[ACCENT_TEXT], background), tint) >= 4.5
