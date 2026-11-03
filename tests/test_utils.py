from __future__ import annotations

from qobit.ui._utils import strip_html


def test_strip_html_removes_tags():
    # Each stripped tag becomes a literal space (not collapsed), so a tag
    # immediately after a space produces two — that's strip_html's actual,
    # documented behavior, not something to paper over here.
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello  world"


def test_strip_html_decodes_entities():
    assert strip_html("Rock &amp; Roll &mdash; the &quot;best&quot;") == 'Rock & Roll — the "best"'


def test_strip_html_collapses_tags_to_single_space():
    assert strip_html("a<br/>b") == "a b"


def test_strip_html_strips_surrounding_whitespace():
    assert strip_html("  <p>padded</p>  ") == "padded"


def test_strip_html_plain_text_unchanged():
    assert strip_html("no markup here") == "no markup here"


def test_strip_html_empty_string():
    assert strip_html("") == ""
