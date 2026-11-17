"""Navigation chrome shared by the main screen and pushed pages: the tab list,
the breadcrumb trail, and the tab bar a pushed page shows.

Model ("pages + trail"): the main screen's tabs can open a detail inline (an
album in Albums, an artist in Artists), and Search, ``g``, ``i`` and Similar
push pages on top. Every level reports where it is with ``nav_levels()`` (the
labels of its own levels, outermost first) and goes back to one of them with
``nav_to(depth)``. The trail is the active tab's name, then the levels of the
tab's view, then those of each pushed page: ``Search › Mogwai › Mr. Beast``.
Click a crumb to go there; Escape goes up one level; ``1``–``7`` (or a click
on a tab) closes the pages and switches tab.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple

from textual import events
from textual.color import Color
from textual.content import Content
from textual.screen import ModalScreen, Screen
from textual.widget import Widget
from textual.widgets import Footer, Tab, Tabs

from .themes import ACCENT_TEXT, BAR_END, BAR_START, HIRES, text_color

if TYPE_CHECKING:
    from .app import QobitApp

# Top bar, left to right: (tab id, display name). The bar shows "[N] Name" and
# the number keys follow this order — both are generated from this list. Ids are
# internal and stable (the Playback tab keeps the id "queue" / QueueView); names
# are display-only — never derive an id from a name.
TABS = [
    ("queue", "Playback"),
    ("tracks", "Tracks"),
    ("artists", "Artists"),
    ("albums", "Albums"),
    ("playlists", "Playlists"),
    ("mixes", "Mixes"),
    ("search", "Search"),
]
TAB_NAMES = dict(TABS)


def tab_label(n: int, name: str, compact: bool) -> str:
    return f"{n} {name}" if compact else f"[{n}] {name}"


# Columns the full "[N] Name" bar needs: the bar's 1-col leading offset, then
# each label plus Tab's 1-col padding on both sides (measured: 90). Narrower
# terminals get the compact "N Name" labels instead of a clipped last tab.
TABBAR_FULL_WIDTH = 1 + sum(
    len(tab_label(n, name, False)) + 2 for n, (_, name) in enumerate(TABS, 1)
)


def make_tabs(compact: bool, prefix: str = "") -> list[Tab]:
    """The tabs, ids ``prefix + tab id``. Pages prefix theirs: a page's own
    widgets may share a tab's id (the artist page's album grid is #albums,
    and took the Albums tab's style)."""
    return [
        Tab(tab_label(n, name, compact), id=prefix + tid) for n, (tid, name) in enumerate(TABS, 1)
    ]


PAGE_TAB_PREFIX = "page-tab-"


class Crumb(NamedTuple):
    label: str
    go: Callable[[], None] | None  # None: where you are (the last crumb)


_SEP = " › "


class Breadcrumb(Widget):
    """The trail up to and including the screen it's on. Recomputed whenever
    the app's ``nav_version`` changes. Hidden on the main screen while you're
    at a tab's top level (the trail would just repeat the tab's name)."""

    DEFAULT_CSS = """
    Breadcrumb {
        height: 1;
        padding: 0 1;
        color: $qobit-muted;
    }
    """

    _spans: list[tuple[int, int, Crumb]] = []

    def on_mount(self) -> None:
        self.watch(self.app, "nav_version", lambda _: self.refresh_trail(), init=True)

    def refresh_trail(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        crumbs = app.nav_trail(self.screen)
        self.display = len(crumbs) > 1 or self.screen is not app.screen_stack[0]
        parts: list[tuple[str, str] | str] = []
        spans: list[tuple[int, int, Crumb]] = []
        x = 0
        for i, crumb in enumerate(crumbs):
            if i:
                parts.append((_SEP, "$qobit-muted"))
                x += len(_SEP)
            last = i == len(crumbs) - 1
            parts.append((crumb.label, "bold $qobit-accent-text" if last else "$qobit-muted"))
            spans.append((x, x + len(crumb.label), crumb))
            x += len(crumb.label)
        self._spans = spans
        self._content = Content.assemble(*parts)
        self.refresh()

    def render(self) -> Content:
        return getattr(self, "_content", Content(""))

    def on_click(self, event: events.Click) -> None:
        x = event.x - self.styles.padding.left
        for start, end, crumb in self._spans:
            if start <= x < end and crumb.go is not None:
                event.stop()
                crumb.go()
                return


class PageTabs(Tabs):
    """The tab bar on a pushed page: the same tabs, the one the page was
    opened from lit. Not focusable (the page's own widgets own the keyboard);
    a click on a tab closes the pages and switches to it, like ``1``–``7``."""

    can_focus = False

    def __init__(self, active: str, compact: bool) -> None:
        super().__init__(*make_tabs(compact, PAGE_TAB_PREFIX), active=PAGE_TAB_PREFIX + active)
        self._armed = False

    def on_mount(self) -> None:
        # Mounting activates the origin tab and posts TabActivated: only
        # activations after that are the user's clicks.
        self.call_after_refresh(setattr, self, "_armed", True)

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        event.stop()
        if self._armed and event.tab is not None and event.tab.id:
            app: QobitApp = self.app  # type: ignore[assignment]
            app.action_switch_tab(event.tab.id.removeprefix(PAGE_TAB_PREFIX))


def page_screens(app: QobitApp) -> list:
    """The pushed pages (not menus or the command palette), bottom to top."""
    return [s for s in app.screen_stack[1:] if not isinstance(s, ModalScreen)]


# The wordmark's colour cycle while playing: one loop (start → end → start)
# every CYCLE_SECS, and the word spans SPREAD of a loop, so it reads like the
# progress bar's gradient sliding along the letters.
_MARK, _NAME = "◢◤", "qobit"
WORDMARK_TEXT = f"{_MARK} {_NAME}"
# What takes one colour each: the mark's two glyphs are one shape and change
# together (cycled per character, the gradient split them); the space has no
# colour to show and is left out.
WORDMARK_UNITS = (_MARK, *_NAME)
CYCLE_SECS = 6.0
SPREAD = 0.5
_FRAME_SECS = 1 / 12
# What the palette reads: also the fallback, and the page text must read on.
_PALETTE_VARS = (BAR_START, BAR_END, ACCENT_TEXT, HIRES, "accent", "background")


def wordmark_palette(app: object) -> tuple[Color, Color] | None:
    """The two colours the wordmark cycles between: the progress bar's ends,
    or (when a theme's bar is one colour) the accent and the HI-RES colour,
    each made readable as text. None for ANSI themes, which can't blend."""
    variables = getattr(app, "theme_variables", {})
    return _palette(tuple((n, variables.get(n, "")) for n in _PALETTE_VARS))


@lru_cache(maxsize=8)
def _palette(items: tuple[tuple[str, str], ...]) -> tuple[Color, Color] | None:
    """wordmark_palette keyed on the theme values it reads: it runs every
    frame and only changes with the theme."""
    variables = dict(items)
    a, b = (text_color(variables, n) for n in (BAR_START, BAR_END))
    if a == b:
        a, b = (text_color(variables, n) for n in (ACCENT_TEXT, HIRES))
    if not (a.startswith("#") and b.startswith("#")):
        return None
    return Color.parse(a), Color.parse(b)


def wordmark_colours(start: Color, end: Color, phase: float, n: int) -> list[str]:
    """One colour per unit (WORDMARK_UNITS) at ``phase`` (0–1) of the cycle."""
    out = []
    for i in range(n):
        x = (phase - SPREAD * i / max(1, n - 1)) % 1.0
        out.append(start.blend(end, 1 - abs(2 * x - 1)).hex)  # triangle: seamless loop
    return out


class Wordmark(Widget):
    """``◢◤ qobit`` at the right end of the tab bar, on every screen. It sits
    on the ``overlay`` layer (QobitApp.CSS), docked right with a height of 1,
    so it shares the tab bar's row instead of taking one of its own.

    While a track plays, its colours cycle through the theme's progress-bar
    gradient (accent ↔ HI-RES where the bar is one colour). The phase comes
    from the clock, so the copies on stacked pages stay in step; the timer
    runs only while playing."""

    DEFAULT_CSS = """
    Wordmark {
        layer: overlay;
        dock: right;
        width: auto;
        height: 1;
        padding: 0 1;
    }
    """

    # The full tab labels plus this mark (and the screen's inset): narrower,
    # the mark would cover the last tab, so it hides.
    MIN_WIDTH = TABBAR_FULL_WIDTH + 12

    def on_mount(self) -> None:
        self.fit(self.app.size.width)
        self._timer = self.set_interval(_FRAME_SECS, self.refresh, pause=True)
        self.watch(self.app, "is_playing", self._sync, init=False)
        self.watch(self.app, "is_paused", self._sync, init=False)
        self._sync()

    def fit(self, width: int) -> None:
        self.display = width >= self.MIN_WIDTH

    def _live(self) -> bool:
        app: QobitApp = self.app  # type: ignore[assignment]
        return app.is_playing and not app.is_paused

    def _sync(self, *_: object) -> None:
        if self._live():
            self._timer.resume()
        else:
            self._timer.pause()
        self.refresh()

    def render(self) -> Content:
        palette = wordmark_palette(self.app) if self._live() else None
        if palette is None:
            return Content.assemble(
                (_MARK, "$qobit-accent-text"), (f" {_NAME}", "bold $qobit-muted")
            )
        phase = (time.monotonic() / CYCLE_SECS) % 1.0
        mark, *letters = zip(
            WORDMARK_UNITS,
            wordmark_colours(*palette, phase, len(WORDMARK_UNITS)),
            strict=True,
        )
        return Content.assemble(
            (mark[0], f"bold {mark[1]}"), " ", *((ch, f"bold {c}") for ch, c in letters)
        )


def page_header(app: QobitApp) -> list[Widget]:
    """What a pushed page composes at its top: the tab bar (origin tab lit),
    the wordmark and the trail."""
    compact = app.size.width < TABBAR_FULL_WIDTH
    return [PageTabs(app.base_tab(), compact), Wordmark(), Breadcrumb()]


class QobitFooter(Footer):
    """Textual's Footer, rebuilt only when the keys it shows change.

    Footer rebuilds all its keys on every bindings update, and a tab switch
    sends four or five (focus leaves, focus enters, the tab bar, the view's
    own ``refresh_bindings``): each rebuild mounted every key again and
    relaid it out. Here the updates of one refresh become one check, and the
    keys are rebuilt only if what they'd show differs."""

    _shown: tuple | None = None
    _pending = False

    def bindings_changed(self, screen: Screen) -> None:
        self._bindings_ready = True
        if not screen.app.app_focus:
            return
        if self.is_attached and screen is self.screen and not self._pending:
            self._pending = True
            self.call_after_refresh(self._rebuild_if_changed)

    def _keys(self) -> tuple:
        return tuple(
            (binding.key, binding.description, binding.action, binding.show, enabled, tooltip)
            for _node, binding, enabled, tooltip in self.screen.active_bindings.values()
        )

    async def _rebuild_if_changed(self) -> None:
        self._pending = False
        if not self.is_attached:
            return
        keys = self._keys()
        if keys != self._shown:
            self._shown = keys
            await self.recompose()
