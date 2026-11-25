"""Cover art with a placeholder.

``Art`` draws its image the way ``_images.image_mode()`` says: with the Kitty
graphics protocol, as coloured half-block characters, or not at all. Until
the image arrives, or when there's none (about 1 in 10 artists has no photo,
and every image with images off), it draws a dithered tile with the
subject's initials instead of an empty gap. It's still a ``TGPImage`` for
queries and CSS (``query_one(TGPImage)``, ``Card TGPImage``).
"""

from __future__ import annotations

import asyncio
from typing import IO

from PIL import Image as PILImage
from rich.console import Console, ConsoleOptions
from rich.console import RenderResult as RichRenderResult
from rich.measure import Measurement
from rich.segment import Segment
from textual.app import RenderResult
from textual.content import Content
from textual_image._terminal import get_cell_size
from textual_image.renderable.halfcell import Image as HalfcellRenderable
from textual_image.renderable.tgp import Image as TGPRenderable
from textual_image.widget import TGPImage

from .._images import image_mode


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


# Pixel rows per cell when the terminal doesn't say (blocks mode, no reply).
_CELL_PX_FALLBACK = 24


def fit_image(image: PILImage.Image, edge: int) -> PILImage.Image:
    """``image`` shrunk so its longest side is at most ``edge`` pixels (as
    is when it already fits)."""
    if max(image.size) <= edge:
        return image
    small = image.copy()
    small.thumbnail((edge, edge), PILImage.Resampling.BILINEAR, reducing_gap=2.0)
    return small


def square_width(image: TGPImage, height: int) -> None:
    """Set ``image``'s width so it renders square at ``height`` rows, from
    the terminal's cell aspect ratio (unknown: left as is)."""
    cell = get_cell_size()
    if cell.width > 0 and cell.height > 0:
        image.styles.width = round(height * cell.height / cell.width)


class Blocks:
    """An image as coloured half blocks (``▀``: two pixels per cell), worked
    out once per size: textual-image's own scales the image again on every
    render."""

    def __init__(
        self, image: PILImage.Image | IO[bytes], width: int | str | None, height: int | str | None
    ) -> None:
        self._image = HalfcellRenderable(image, width, height)
        self._size: tuple[int, int | None] | None = None
        self._segments: list[Segment] = []

    def cleanup(self) -> None:
        pass

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RichRenderResult:
        size = (options.max_width, options.max_height)
        if size != self._size:
            self._segments = list(self._image.__rich_console__(console, options))  # type: ignore[arg-type]  # textual-image yields Segments
            self._size = size
        yield from self._segments

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return self._image.__rich_measure__(console, options)


class Art(TGPImage, Renderable=TGPRenderable):
    """Cover art; a ░ tile with ``placeholder``'s initials until it loads.
    The initials take the widget's CSS colour (the accent; the selection's
    text colour on a selected card)."""

    DEFAULT_CSS = """
    Art { color: $qobit-accent-text; }
    """

    _rows: int | None = None

    def __init__(self, placeholder: str = "", *, rows: int | None = None, **kwargs: object) -> None:
        """``rows``: the fixed height the art always has (a grid card's). Its
        image is then kept at that size, not the 600 px cover: a grid of
        cards held two full copies each (1.1 GB after scrolling 300
        albums), and every render rescaled the full one."""
        self._rows = rows
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._placeholder = placeholder
        self._drawn: tuple[object, ...] | None = None  # (mode, size) of _renderable

    def _edge(self) -> int | None:
        """The longest side, in pixels, the image is kept at (None: as is)."""
        if self._rows is None:
            return None
        cell = get_cell_size()
        return self._rows * (cell.height if cell.height > 0 else _CELL_PX_FALLBACK)

    @property  # type: ignore[override]
    def image(self) -> PILImage.Image | None:
        return TGPImage.image.fget(self)  # type: ignore[attr-defined]

    @image.setter
    def image(self, value: PILImage.Image | None) -> None:
        edge = self._edge()
        if edge is not None and isinstance(value, PILImage.Image):
            value = fit_image(value, edge)  # a no-op after show()
        TGPImage.image.fset(self, value)  # type: ignore[attr-defined]

    async def show(self, image: PILImage.Image) -> None:
        """Set ``image``, shrunk to this art's size in a worker thread (a
        grid page of covers resized at once stalled the UI on slow CPUs)."""
        edge = self._edge()
        if edge is not None:
            image = await asyncio.to_thread(fit_image, image, edge)
        if self.is_attached:
            self.image = image

    @property
    def placeholder(self) -> str:
        return self._placeholder

    @placeholder.setter
    def placeholder(self, text: str) -> None:
        self._placeholder = text
        if not self.image:
            self.refresh()

    def render(self) -> RenderResult:
        mode = image_mode()
        if self.image and mode != "off":
            return self._image_renderable(mode)
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

    def _image_renderable(self, mode: str) -> TGPRenderable | Blocks:
        """The image's renderable, kept while the image, its size and the mode
        stay the same. textual-image builds a new one on every render: each
        repaint (a grid card selected or deselected, a theme change) scaled,
        PNG-encoded and re-sent the whole image to the terminal. A new image
        resets ``_renderable`` (the ``image`` setter)."""
        size = self._get_styled_size()
        if self._renderable is None or self._drawn != (mode, size):
            if self._renderable is not None:
                self._renderable.cleanup()  # frees a Kitty image in the terminal
            if mode == "kitty":
                self._renderable = TGPRenderable(self.image, *size)  # type: ignore[arg-type]
            else:
                self._renderable = Blocks(self.image, *size)  # type: ignore[arg-type]
            self._drawn = (mode, size)
        return self._renderable  # type: ignore[return-value]

    def on_unmount(self) -> None:
        # The terminal keeps a Kitty image until told to drop it.
        if self._renderable is not None:
            self._renderable.cleanup()
            self._renderable = None
