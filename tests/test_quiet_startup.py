"""textual-image's "Failed to get cell size" warning (with a traceback)
printed on the terminal before the TUI started, wherever the terminal
doesn't answer its query: the Linux console of a Raspberry Pi, for one."""

from __future__ import annotations

import logging

import qobit.ui  # noqa: F401  (sets the level on import)


def test_textual_image_warnings_are_silenced():
    assert not logging.getLogger("textual_image._terminal").isEnabledFor(logging.WARNING)
