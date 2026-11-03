from __future__ import annotations

import html as _html
import re

# Responsive thresholds shared by every album-detail-based view (AlbumsView,
# ArtistsView, AlbumScreen). As the terminal gets shorter the layout
# progressively reclaims space: hide the artist header, then halve the album
# art, then switch the tracklist to two columns.
HIDE_ARTIST_BELOW = 32
SMALL_ART_BELOW = 24
TWO_COL_BELOW = 17


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
    background: $boost;
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
.detail-sub   { height: auto; color: $text-muted; margin-top: 1; }
.detail-desc  { height: auto; color: $text-muted; margin-top: 1; }
.detail-tracklist {
    height: 1fr;
    border-top: solid $accent 40%;
}
"""
