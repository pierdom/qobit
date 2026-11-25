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


# ── the shared UI layer imports nothing app- or Qobuz-specific ──────────────
#
# These modules are what another app on the same look would reuse: themes,
# nav (tabs, breadcrumb, wordmark, footer), the transport bar, cover art and
# image fetching, the list widgets and the now-playing/filter mixins. At run
# time they import only Textual, the standard library and each other. A hint
# for the type checker (under TYPE_CHECKING) may name the app.

SHARED = [
    UI / "themes.py",
    UI / "nav.py",
    UI / "_now_playing.py",
    UI / "_filter.py",
    UI / "_utils.py",
    UI / "_images.py",
    UI / "widgets" / "transport.py",
    UI / "widgets" / "art.py",
    UI / "widgets" / "lists.py",
    UI / "widgets" / "grid.py",
]
APP_SPECIFIC = (
    "qobit.qobuz",
    "qobit.connect",
    "qobit.lyrics",
    "qobit.ui.app",
    "qobit.ui.screens",
    "qobit.ui.play_context",
    "qobit.config",
    "qobit.store",
    "qobit.library_cache",
    "qobit.account_data",
    "qobit.token_store",
)


def _runtime_imports(path: Path, package: list[str] | None = None) -> list[tuple[int, str]]:
    """(line, absolute module) of every import not under ``if TYPE_CHECKING``.
    ``package`` is the file's package (default: from its place under ``src/qobit``)."""
    package = package or ["qobit", *path.relative_to(UI.parent).parent.parts]
    tree = ast.parse(path.read_text())
    skip = {
        id(child)
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test)
        for child in ast.walk(node)
    }
    found = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Import):
            found.extend((node.lineno, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            found.append((node.lineno, module))
            found.extend((node.lineno, f"{module}.{a.name}") for a in node.names)
    return found


@pytest.mark.parametrize("path", SHARED, ids=lambda p: str(p.relative_to(UI)))
def test_the_shared_ui_modules_import_nothing_app_specific(path):
    bad = [
        (line, mod)
        for line, mod in _runtime_imports(path)
        if any(mod == p or mod.startswith(p + ".") for p in APP_SPECIFIC)
    ]
    assert bad == [], f"{path.name} imports app-specific code at run time: {bad}"


def test_the_import_check_sees_a_runtime_import_and_skips_type_checking(tmp_path):
    src = (
        "from typing import TYPE_CHECKING\n"
        "from ..qobuz.models import Track\n"
        "if TYPE_CHECKING:\n"
        "    from .app import QobitApp\n"
        "def f():\n"
        "    from .screens.search import X\n"
    )
    probe = tmp_path / "probe.py"
    probe.write_text(src)
    mods = [m for _, m in _runtime_imports(probe, ["qobit", "ui"])]
    assert "qobit.qobuz.models" in mods and "qobit.ui.screens.search" in mods
    assert not any(m.startswith("qobit.ui.app") for m in mods)


# ── nothing below the UI imports Textual ────────────────────────────────────
#
# The layers under ui/ (docs/architecture.md) are testable on their own and
# take what they need from the app by injection (a worker runner, a callback).

NO_TEXTUAL = [
    "qobit.library",
    "qobit.library_cache",
    "qobit.config",
    "qobit.store",
    "qobit.doctor",
    "qobit.qobuz.client",
    "qobit.qobuz.models",
    "qobit.qobuz.streams",
    "qobit.qobuz.suggestions",
    "qobit.audio.player",
    "qobit.audio.watch",
    "qobit.audio.verify",
    "qobit.audio.device",
    "qobit.lyrics",
    "qobit.connect",
]


def _imports_textual(modules: list[str]) -> bool:
    import subprocess
    import sys

    code = "import sys, " + ", ".join(modules) + "; sys.exit('textual' in sys.modules)"
    return subprocess.run([sys.executable, "-c", code]).returncode != 0


@pytest.mark.parametrize("module", NO_TEXTUAL)
def test_the_layers_under_the_ui_do_not_import_textual(module):
    assert not _imports_textual([module]), f"{module} imports Textual"


def test_the_textual_check_sees_a_module_that_does():
    assert _imports_textual(["qobit.ui._utils"])
