from __future__ import annotations

import html as _html
import re

_BLOCK_TAG = re.compile(r"</?(?:p|br|div|li|ul|ol|h[1-6]|tr|td|blockquote)\b[^>]*>", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")


def within(widget: object, css_class: str) -> bool:
    """Whether ``widget`` (e.g. a click's) is, or sits inside, a widget with
    ``css_class``."""
    return widget is not None and any(
        w.has_class(css_class)
        for w in widget.ancestors_with_self  # type: ignore[attr-defined]
    )


def focus_later(view: object, focus: object) -> None:
    """Call ``focus`` after the next refresh if ``view`` (a tab's view) is
    still the one shown. A busy view's deferred focus could run after the
    user had switched tabs and pull the focus back to a hidden list: the
    Playback tab's startup focus did, behind a quick ``4``."""

    def run() -> None:
        if view.is_attached and view.display:  # type: ignore[attr-defined]
            focus()  # type: ignore[operator]

    view.call_after_refresh(run)  # type: ignore[attr-defined]


def set_own_class(widget: object, add: bool, css_class: str) -> None:
    """Toggle a class that only ``widget``'s own rules use (a container's
    border), restyling just the widget. ``set_class`` restyles everything
    inside it: a whole track list on each focus change."""
    if widget.has_class(css_class) == add:  # type: ignore[attr-defined]
        return
    widget.set_class(add, css_class, update=False)  # type: ignore[attr-defined]
    widget.app.stylesheet.update_nodes([widget], animate=True)  # type: ignore[attr-defined]


def strip_html(text: str) -> str:
    """Strip HTML tags and decode HTML entities to plain text. Block and
    break tags (p, br, div, li) separate words with a space; inline ones
    (b, i, a, span…) just go ("<b>Alton</b>, IL" read "Alton , IL").
    Runs of spaces collapse to one; line breaks stay."""
    spaced = _BLOCK_TAG.sub(" ", text)
    plain = _html.unescape(_ANY_TAG.sub("", spaced))
    return re.sub(r"[ \t]+", " ", plain).strip()


# Shared "art + metadata header" layout used by both AlbumDetailPanel
# (album_detail.py) and PlaylistScreen (playlist_detail.py): art on the left,
# a scrollable title/subtitle/description column on the right, a tracklist
# below. Each widget concatenates this onto its own DEFAULT_CSS and composes
# with these class names (.detail-header/-art/-meta/-title/-sub/-desc/
# -tracklist); anything specific to one of them (e.g. album version/badges/
# awards) stays local to that widget's own CSS.
DETAIL_HEADER_CSS = """
.detail-header {
    height: 16;
    layout: horizontal;
    padding: 1 2;
}
.detail-art {
    width: 16;
    height: 14;
    margin-right: 2;
}
.detail-meta { width: 1fr; height: 14; }
/* Constrain the text rows to the meta column so long text wraps instead of
   overflowing to the right (the description in particular). */
.detail-meta Label { width: 1fr; }
.detail-title { height: auto; text-style: bold; }
.detail-sub   { height: auto; color: $qobit-muted; margin-top: 1; }
.detail-desc  { height: auto; color: $qobit-muted; margin-top: 1; }
.detail-tracklist {
    height: 1fr;
    border-top: round $accent 40%;
}
/* A short panel (album_detail.fit_detail_panel): no art, the header down to
   the title and the info line. */
.detail-header.-no-art { padding: 0 2; }
.detail-header.-no-art .detail-art { display: none; }
.detail-header.-no-art .detail-sub { margin-top: 0; }
.detail-header.-no-art .detail-meta { overflow-y: hidden; }
"""


def status_line(text: str, *, error: bool = False, id: str | None = None):  # -> Label
    """The one look for an empty or failed list/grid: a muted line (``$error``
    for failures; styled in QobitApp.CSS). Plain text, never markup: an
    exception message or a search query may contain ``[``."""
    from textual.widgets import Label

    return Label(
        text, classes="status-line -error" if error else "status-line", id=id, markup=False
    )


def readout(text: str) -> str:
    """A panel title in qobit's readout style: uppercase, like the queue's
    ``── UP NEXT ──`` dividers and the transport's ``[ ▶ PLAYING ]``. Keeps
    the length (a letter whose uppercase is longer, like ß, stays as is):
    clickable titles map a click to a name by its position."""
    return "".join(u if len(u := c.upper()) == 1 else c for c in text)
