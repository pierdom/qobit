"""The helpers that the type cleanup introduced behave as plain Python."""

from __future__ import annotations

import subprocess
import sys

from qobit.secret import Secret, secret
from qobit.ui import _utils
from qobit.ui._filter import TypeToFilter
from qobit.ui._now_playing import NowPlayingRowMixin, NowPlayingViewMixin
from qobit.ui.screens.artist_detail import _GridNav


def test_the_mixins_are_plain_classes_at_run_time():
    """They derive from ``WidgetBase`` for the checker only: at run time it is
    ``object``, so a mixin never brings Widget into the MRO a second time."""
    assert _utils.WidgetBase is object
    for mixin in (NowPlayingRowMixin, NowPlayingViewMixin, TypeToFilter, _GridNav):
        assert mixin.__mro__ == (mixin, object)


def test_qapp_returns_the_nodes_app_unchanged():
    sentinel = object()

    class Node:
        app = sentinel

    assert _utils.qapp(Node()) is sentinel  # type: ignore[arg-type]


def test_secret_keeps_none_and_empty_and_masks_the_rest():
    assert secret(None) is None
    assert secret("") == ""
    token = secret("abc")
    assert isinstance(token, Secret) and token == "abc" and repr(token) == "'<redacted>'"


def test_the_shared_layer_still_imports_without_the_app():
    """qapp casts by string: importing _utils must not import the app."""
    code = "import sys; import qobit.ui._utils; assert 'qobit.ui.app' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)
