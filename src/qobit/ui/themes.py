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

from collections.abc import Mapping

from textual.color import Color
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
# The rest are computed per theme by qobit_variable_defaults (a theme may still
# set them): readable pairs, measured, not assumed.
SELECT_TEXT = "qobit-select-text"  # all text on a selection
SELECT_DIM = "qobit-select-dim"  # an unfocused list's highlighted row
NOW = "qobit-now"  # the now-playing row's tint
ACCENT_TEXT = "qobit-accent-text"  # accent-coloured text (the playing row)
MUTED = "qobit-muted"  # secondary text (meta lines, inactive tabs, hints)

_MIN_CONTRAST = 4.5  # WCAG AA for body text


def contrast(a: Color, b: Color) -> float:
    """WCAG contrast ratio of two opaque colours (1 to 21)."""

    def lum(c: Color) -> float:
        def channel(v: int) -> float:
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b)

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _readable(colour: Color, bg: Color) -> Color:
    """``colour``, moved toward black or white just enough to read on ``bg``."""
    target = bg.get_contrast_text(1.0)
    for step in range(11):
        candidate = colour.blend(target, step / 10)
        if contrast(candidate, bg) >= _MIN_CONTRAST:
            return candidate
    return target


def qobit_variable_defaults(theme: Theme) -> dict[str, str]:
    """qobit's variables for ``theme``, unless it sets them itself.

    Truecolor themes get colours computed from the theme and checked for
    contrast: the selection is the accent at 35% over the background, and its
    text the first of accent, foreground, black/white that reads on it. ANSI
    themes can't blend (an 8% tint came out as solid accent, the text on it in
    the same colour): they get Textual's own block-cursor pair and no tints.
    """
    accent_name = theme.accent or theme.primary
    if theme.ansi:
        return {
            BAR_START: accent_name,
            BAR_END: accent_name,
            HIRES: theme.warning or accent_name,
            HEART: accent_name,
            READOUT: accent_name,
            ACCENT_TEXT: accent_name,
            SELECT: theme.variables.get("block-cursor-background", "ansi_white"),
            SELECT_TEXT: theme.variables.get("block-cursor-foreground", "ansi_black"),
            SELECT_DIM: "transparent",
            NOW: "transparent",
            # Grey reads on a dark terminal; on a light one only the default does.
            MUTED: "ansi_bright_black" if theme.dark else "ansi_default",
        }
    # The colours Textual actually generates for the theme (a theme may leave
    # its background unset: textual-light's is #E0E0E0, not the default).
    system = theme.to_color_system().generate()
    bg = Color.parse(system["background"])
    surface = Color.parse(system["surface"])
    accent = Color.parse(system["accent"])
    fg = Color.parse(system["foreground"])
    own = theme.variables.get(SELECT)
    tint = Color.parse(own) if own else accent.with_alpha(0.35)
    select = bg.blend(tint.with_alpha(1), tint.a)
    candidates = (accent, fg, select.get_contrast_text(1.0))
    select_text = next(
        (c for c in candidates if contrast(c, select) >= _MIN_CONTRAST),
        max(candidates, key=lambda c: contrast(c, select)),
    )
    now = bg.blend(accent, 0.08)
    # Readable on the now-playing row's tint as well as on the page (the
    # playing row's text sits on the tint).
    select_dim = bg.blend(accent, 0.10)
    # Every surface text sits on: the page, the playing row's tint, an
    # unfocused highlight and the i/g menu ($surface).
    surfaces = (bg, now, select_dim, surface)
    accent_text = accent
    for under in surfaces:
        accent_text = _readable(accent_text, under)
    # Secondary text: the theme's own muted colour if it sets a real one
    # (cyberdeck), else the foreground at 60%, then moved until it reads on
    # the page, an unfocused highlight and the playing row. Never Textual's
    # "auto 60%": that picks black or white against the widget's background,
    # which with the theme background off is the terminal's, assumed dark, so
    # light themes got white-on-white muted text.
    own_muted = theme.variables.get("text-muted", "")
    muted = Color.parse(own_muted) if own_muted.startswith("#") else bg.blend(fg, 0.6)
    for under in surfaces:
        muted = _readable(muted, under)
    warning = Color.parse(system["warning"])
    return {
        BAR_START: accent.hex,
        BAR_END: accent.hex,
        HIRES: _readable(warning, bg).hex,
        HEART: accent_text.hex,
        READOUT: accent_text.hex,
        ACCENT_TEXT: accent_text.hex,
        SELECT: select.hex,
        SELECT_TEXT: select_text.hex,
        SELECT_DIM: select_dim.hex,  # 10%: 12% dimmed muted text below 4.5:1
        MUTED: muted.hex,
        NOW: now.hex,
    }


def theme_color(app: object, name: str, fallback: str = "accent") -> str:
    """A theme variable's colour, falling back to a standard one."""
    variables = getattr(app, "theme_variables", {})
    return variables.get(name) or variables.get(fallback, "")


def theme_text_color(app: object, name: str, fallback: str = "accent") -> str:
    """A theme colour used as text, moved until it reads on the page (the
    lamp's success/error green and red were 1.4:1 on light themes). ANSI
    colours pass through: the terminal's palette decides."""
    return text_color(getattr(app, "theme_variables", {}), name, fallback)


def text_color(variables: Mapping[str, str], name: str, fallback: str = "accent") -> str:
    """theme_text_color on a theme's variables (for callers that cache)."""
    value = variables.get(name) or variables.get(fallback, "")
    background = variables.get("background", "")
    if value.startswith("#") and background.startswith("#"):
        return _readable(Color.parse(value), Color.parse(background)).hex
    return value


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
    # Ayu blue, darkened to read on the light background (#22A4E6 was 2.65:1).
    variables={"boost": "#F4F5F6", HIRES: "#1772A1"},
)

QOBIT_THEMES = [CYBERDECK_THEME, AYU_DARK_THEME, AYU_MIRAGE_THEME, AYU_LIGHT_THEME]
