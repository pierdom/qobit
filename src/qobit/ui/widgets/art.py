"""Cover art with a placeholder.

``Art`` is a Kitty-protocol image (``TGPImage``) that, until its image
arrives, or when there's none (about 1 in 10 artists has no photo), draws a
dithered tile with the subject's initials instead of an empty gap. It's still
a ``TGPImage`` for queries and CSS (``query_one(TGPImage)``, ``Card TGPImage``).
"""

from __future__ import annotations

from textual.app import RenderResult
from textual.content import Content
from textual_image._terminal import get_cell_size
from textual_image.renderable.tgp import Image as TGPRenderable
from textual_image.widget import TGPImage


def initials(text: str) -> str:
    """Up to two letters: the first of the first two words ("PJ Harvey" →
    "PH"), or the first two of a single word ("Mogwai" → "MO"). A leading
    article is skipped ("The Velvet Underground" → "VU")."""
    words = [w for w in text.split() if w[:1].isalnum()]
    if len(words) > 1 and words[0].lower() in ("the", "a", "an"):
        words = words[1:]
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def square_width(image: TGPImage, height: int) -> None:
    """Set ``image``'s width so it renders square at ``height`` rows, from
    the terminal's cell aspect ratio (unknown: left as is)."""
    cell = get_cell_size()
    if cell.width > 0 and cell.height > 0:
        image.styles.width = round(height * cell.height / cell.width)


class Art(TGPImage, Renderable=TGPRenderable):
    """Cover art; a ░ tile with ``placeholder``'s initials until it loads.
    The initials take the widget's CSS colour (the accent; the selection's
    text colour on a selected card)."""

    DEFAULT_CSS = """
    Art { color: $qobit-accent-text; }
    """

    def __init__(self, placeholder: str = "", **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._placeholder = placeholder

    @property
    def placeholder(self) -> str:
        return self._placeholder

    @placeholder.setter
    def placeholder(self, text: str) -> None:
        self._placeholder = text
        if not self.image:
            self.refresh()

    def render(self) -> RenderResult:
        if self.image:
            return super().render()
        width, height = self.content_size
        if width <= 0 or height <= 0:
            return ""
        mark = initials(self._placeholder)[: max(0, width - 2)]
        middle = height // 2
        # Glyphs only, no background (qobit's sections paint none, and this
        # stays the same with the theme background off): a ░ texture in the
        # panel colour, the initials in the accent.
        texture, letters = "$panel-lighten-2", "bold"
        lines: list[Content] = []
        for y in range(height):
            if y == middle and mark:
                left = (width - len(mark)) // 2
                lines.append(
                    Content.assemble(
                        ("░" * left, texture),
                        (mark, letters),
                        ("░" * (width - left - len(mark)), texture),
                    )
                )
            else:
                lines.append(Content.styled("░" * width, texture))
        return Content("\n").join(lines)
