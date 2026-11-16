from __future__ import annotations

from qobit.ui._utils import strip_html


def test_strip_html_removes_tags():
    # A tag next to a space no longer leaves two: they showed on screen
    # ("User-written  text" in a playlist description).
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_keeps_line_breaks():
    assert strip_html("one\n\ntwo <i>three</i>") == "one\n\ntwo three"


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


def test_strip_html_inline_tags_leave_no_space():
    html = "Born in <b>Alton</b>, <a href='x'>Illinois</a>."
    assert strip_html(html) == "Born in Alton, Illinois."


def test_strip_html_block_tags_separate_words():
    assert strip_html("<p>One.</p><p>Two.</p>") == "One. Two."
    assert strip_html("a<BR>b<li>c</li>") == "a b c"
