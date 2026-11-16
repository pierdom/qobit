from __future__ import annotations

import html as _html
import re


def strip_html(text: str) -> str:
    """Strip HTML tags and decode HTML entities to plain text."""
    return _html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


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
