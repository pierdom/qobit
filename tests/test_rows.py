"""Track-row gutters (ui/_now_playing): every row's text, and its meta line,
start in the same column, and the ▶/⏸ marker never shifts a row."""

from __future__ import annotations

from qobit.ui._now_playing import (
    GUTTER_ICON,
    GUTTER_NUM,
    GUTTER_NUM_LONG,
    gutter,
    meta_indent,
    playing_content,
)


def test_gutter_right_aligns_the_lead():
    assert gutter("♪", GUTTER_ICON) == "♪  "
    assert gutter("1.", GUTTER_NUM) == " 1.  "
    assert gutter("12.", GUTTER_NUM) == "12.  "
    assert gutter("123.", GUTTER_NUM_LONG) == "123.  "


def test_meta_line_starts_under_the_title_text():
    for width in (GUTTER_ICON, GUTTER_NUM, GUTTER_NUM_LONG):
        assert len(meta_indent(width)) == len(gutter("x", width))


def test_playing_marker_replaces_the_lead_without_shifting():
    idle = playing_content("7.", "Title", False, False, width=GUTTER_NUM).plain
    playing = playing_content("7.", "Title", True, False, width=GUTTER_NUM).plain
    paused = playing_content("7.", "Title", True, True, width=GUTTER_NUM).plain
    assert idle == " 7.  Title"
    assert playing == "  ▶  Title"
    assert paused == "  ⏸  Title"
    assert idle.index("Title") == playing.index("Title") == paused.index("Title")


def test_suffix_is_appended():
    content = playing_content("♪", "Song", False, False, ("  ♥", "$accent"))
    assert content.plain == "♪  Song  ♥"
