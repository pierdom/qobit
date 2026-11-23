"""Screens and widgets read the app through its public names and reactives,
never through its underscore state (QobitApp's "what screens and widgets read"
block). The one exception is the Qobuz client: every screen asks it for data.

The play contexts (ui/play_context.py) and app.py itself are the app's
collaborators and may touch its internals: they are not checked here.
"""

import ast
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "src" / "qobit" / "ui"
CHECKED = [
    *sorted((UI / "screens").glob("*.py")),
    *sorted((UI / "widgets").glob("*.py")),
    UI / "nav.py",
    UI / "_now_playing.py",
    UI / "_filter.py",
    UI / "_utils.py",
    UI / "_images.py",
]
ALLOWED = {"_client"}


def _is_app(node: ast.expr) -> bool:
    """``app`` or ``self.app`` (``self._app`` too)."""
    if isinstance(node, ast.Name):
        return node.id == "app"
    return (
        isinstance(node, ast.Attribute)
        and node.attr in ("app", "_app")
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _private_reads(path: Path) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if (
            isinstance(node, ast.Attribute)
            and _is_app(node.value)
            and node.attr.startswith("_")
            and not node.attr.startswith("__")
            and node.attr not in ALLOWED
        ):
            found.append((node.lineno, node.attr))
    return found


@pytest.mark.parametrize("path", CHECKED, ids=lambda p: str(p.relative_to(UI)))
def test_screens_and_widgets_do_not_read_the_apps_private_state(path):
    assert _private_reads(path) == [], (
        f"{path.name} reads app internals: use a public name on QobitApp "
        "(history, up_next, current_track, favorite_*, last_stream...)"
    )


def test_the_check_sees_a_private_read(tmp_path):
    """The test must be able to fail: a made-up widget that reads app._cursor."""
    src = "class W:\n    def f(self):\n        return self.app._cursor + app._timeline\n"
    path = tmp_path / "probe.py"
    path.write_text(src)
    assert [attr for _, attr in _private_reads(path)] == ["_cursor", "_timeline"]
