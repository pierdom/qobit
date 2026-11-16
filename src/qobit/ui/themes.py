"""qobit's own colour themes, registered on the app and listed in the theme
picker (Ctrl+P → Theme) next to Textual's built-ins. The choice is saved.

qobit draws everything from theme variables — there are no hard-coded colours.
Colour roles: ``$accent`` is the chrome (borders, focus, selection, now
playing); ``qobit-hires`` and ``qobit-heart`` mark signal (HI-RES, favourites);
``qobit-readout`` is the transport's status and time readout, like an LED
display. In cyberdeck those are cyan, neon pink and amber.

Textual drops a theme's ``boost`` whenever it also sets ``panel`` ($boost
came out transparent, so every $boost band was invisible): each theme sets it
again through ``variables``.
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
HEART = "qobit-heart"  # ♥ on favourite tracks and followed artists (default: $accent)
READOUT = "qobit-readout"  # transport status tag and time (default: $accent)
SELECT = "qobit-select"  # selected row / card background (default: $accent 35%)


def theme_color(app: object, name: str, fallback: str = "accent") -> str:
    """A theme variable's colour, falling back to a standard one."""
    variables = getattr(app, "theme_variables", {})
    return variables.get(name) or variables.get(fallback, "")


CYBERDECK_THEME = Theme(
    # Neon noir meets cassette futurism: cyan chrome on a violet-black night,
    # neon pink for signal (HI-RES, hearts), amber LED readouts (transport
    # status, time, footer keys) as on a tape deck or a CRT terminal.
    name="qobit-cyberdeck",
    primary="#ff2ec4",  # hot magenta: Textual's own chrome (palette, focus)
    secondary="#8f5cff",  # electric violet (4.9:1 on the background)
    accent="#00e5ff",  # electric cyan: qobit's chrome
    warning="#ffb000",  # amber
    error="#ff2965",  # hot red
    success="#39ff88",  # acid green
    foreground="#d6faff",  # pale cyan-white body text (18:1)
    background="#0a0612",  # near-black with a violet undertone
    surface="#140d24",
    panel="#2f2252",
    dark=True,
    variables={
        "boost": "#231a3d",  # see the module docstring
        # Muted text tinted cyan instead of Textual's neutral white 60%:
        # 10.7:1 on the background, 5.7:1 on a selected row.
        "text-muted": "#9ec3d2",
        "footer-key-foreground": "#ffb000",  # amber keys on the violet footer
        BAR_START: "#00e5ff",  # the progress bar fades cyan → magenta
        BAR_END: "#ff2ec4",
        # Neon pink, a touch lighter than primary: still readable (and bold)
        # on a selected row.
        HIRES: "#ff5cd3",
        HEART: "#ff2ec4",
        READOUT: "#ffb000",
        SELECT: "#00e5ff47",  # cyan 28%: 35% made pink text on it hard to read
    },
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
    dark=True,
    variables={"boost": "#12161E", HIRES: "#59C2FF"},  # Ayu blue (its secondary)
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
    dark=True,
    variables={"boost": "#262C38", HIRES: "#73D0FF"},  # Ayu blue (its secondary)
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
    dark=False,
    variables={"boost": "#F4F5F6", HIRES: "#22A4E6"},  # Ayu blue (its secondary)
)

QOBIT_THEMES = [CYBERDECK_THEME, AYU_DARK_THEME, AYU_MIRAGE_THEME, AYU_LIGHT_THEME]
