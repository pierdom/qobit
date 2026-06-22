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
