"""qobit's own colour themes, registered on the app and listed in the theme
picker (Ctrl+P → Theme) next to Textual's built-ins. The choice is saved.

qobit draws everything from theme variables — there are no hard-coded colours —
and uses ``$accent`` for its own chrome (borders, now-playing, selection).
"""

from __future__ import annotations

from textual.theme import Theme

# qobit's own theme variables (Theme.variables). Themes that don't set them,
# Textual's built-ins included, get QobitApp.get_theme_variable_defaults(), so
# they're safe in CSS and Content styles ($qobit-hires). Rich Text and border
# titles don't resolve $variables: read the value with theme_color().
BAR_START = "qobit-bar-start"  # progress bar, left end (default: $accent)
BAR_END = "qobit-bar-end"  # progress bar, right end: a gradient if it differs
HIRES = "qobit-hires"  # the HI-RES tag (default: $warning)


def theme_color(app: object, name: str, fallback: str = "accent") -> str:
    """A theme variable's colour, falling back to a standard one."""
    variables = getattr(app, "theme_variables", {})
    return variables.get(name) or variables.get(fallback, "")


CYBERDECK_THEME = Theme(
    name="qobit-cyberdeck",
    primary="#ff2ec4",  # hot magenta — Textual's own chrome (palette, focus)
    secondary="#6e3bff",  # electric violet — secondary emphasis
    accent="#00e5ff",  # electric cyan — qobit's own $accent: borders, now-playing,
    # selection, hearts
    warning="#ffb000",  # amber — CRT-terminal warning colour
    error="#ff2965",  # hot red
    success="#39ff88",  # acid green
    foreground="#d6faff",  # pale cyan-white body text
    background="#0a0612",  # near-black with a violet undertone
    surface="#140d24",
    panel="#2f2252",
    boost="#231a3d",
    dark=True,
    # The two neons meet in the progress bar: cyan fading into magenta, and
    # hi-res tagged in magenta against the cyan chrome.
    variables={BAR_START: "#00e5ff", BAR_END: "#ff2ec4", HIRES: "#ff2ec4"},
)

# Ayu — https://github.com/ayu-theme/ayu-colors (v6, themes/*.yaml).
# background = ui.bg (surface.base), surface = editor.bg (surface.lift),
# panel = ui.panel.bg, accent/primary = common.accent.tint, error =
# common.error, success = vcs.added, secondary/warning = palette blue/orange.
# Ayu's foreground is translucent (e.g. FCF8EC at 74%); it's pre-blended over
# the editor background, which yields Ayu's published text colours exactly.
# boost sits halfway between surface and panel.

AYU_DARK_THEME = Theme(
    name="ayu-dark",
    primary="#E6B450",
    secondary="#59C2FF",
    accent="#E6B450",
    warning="#FF8F40",
    error="#D95757",
    success="#70BF56",
    foreground="#BFBDB6",
    background="#0D1017",
    surface="#10141C",
    panel="#141821",
    boost="#12161E",
    dark=True,
    variables={HIRES: "#59C2FF"},  # Ayu blue (its secondary)
)

AYU_MIRAGE_THEME = Theme(
    name="ayu-mirage",
    primary="#FFCC66",
    secondary="#73D0FF",
    accent="#FFCC66",
    warning="#FFA659",
    error="#FF6666",
    success="#87D96C",
    foreground="#CCCAC2",
    background="#1F2430",
    surface="#242936",
    panel="#282E3B",
    boost="#262C38",
    dark=True,
    variables={HIRES: "#73D0FF"},  # Ayu blue (its secondary)
)

AYU_LIGHT_THEME = Theme(
    name="ayu-light",
    primary="#F29718",
    secondary="#22A4E6",
    accent="#F29718",
    warning="#FA8532",
    error="#E65050",
    success="#6CBF43",
    foreground="#5C6166",
    background="#F8F9FA",
    surface="#FCFCFC",
    panel="#EBEEF0",
    boost="#F4F5F6",
    dark=False,
    variables={HIRES: "#22A4E6"},  # Ayu blue (its secondary)
)

QOBIT_THEMES = [CYBERDECK_THEME, AYU_DARK_THEME, AYU_MIRAGE_THEME, AYU_LIGHT_THEME]
