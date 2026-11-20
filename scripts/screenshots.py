"""Render the README screenshots (screenshots/*.png) from the real UI.

Runs qobit's own app headless, against your saved Qobuz session (log in
first: ``qobit auth``), and draws its screen to a PNG cell by cell, with the
cover art pasted over the image cells at full resolution, as a Kitty-protocol
terminal shows them. Not a terminal capture: no terminal is involved.

What it shows: the cyberdeck theme, The Verve's "Bitter Sweet Symphony" playing
(no audio is started: the play state is set, the track, album, bio and lyrics
are the real ones: the lyrics come from LRCLIB, the one request that isn't to
Qobuz), a progress bar past 75% so its gradient shows, and your real favourites
in the library tabs. The track must be one of your favourite tracks. Ty
Segall's page is opened from Artists (it must be one of your
followed artists; else the first one is used), and his album Possession from
Albums (same rule).

Everything it writes (settings, queue, covers) goes to a temporary folder, not
to your qobit files. It reads your session and makes read-only API calls.

    uv run --with matplotlib python scripts/screenshots.py

matplotlib is only there for the DejaVu Sans Mono font it ships; or pass
``--font-dir`` with DejaVuSansMono*.ttf (Regular, Bold, Oblique, BoldOblique).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from rich.cells import cell_len
from rich.color import Color as RichColor
from rich.terminal_theme import DEFAULT_TERMINAL_THEME
from textual.pilot import Pilot

COLS, ROWS = 132, 40  # wide enough for the artist page's two track lists + Similar
CELL_W, CELL_H = 15, 32  # a 2x capture of a 7.5 x 16 px cell
FONT_PX = 25  # DejaVu Sans Mono's advance at this size: 15 px

NOW_PLAYING = ("Hole", "Violet")  # timed lyrics running past 75% of the track
ARTIST_PAGE = "Ty Segall"
ALBUM_PAGE = ("Ty Segall", "Possession")  # an English bio, and a HI-RES readout
# Scenes that need another size: the Playback tab is 160 wide so the queue, the
# now-playing panel and the lyrics sit side by side (at 132 the last two stack
# and the cover has no room); 48 rows keeps the picture's proportions.
SCENE_SIZE = {"queue_page": (160, 48)}
PLAYED_FRACTION = 0.8  # of the track: past 75%, where the bar's gradient shows

RGB = tuple[int, int, int]


# ── drawing ──────────────────────────────────────────────────────────────────


def find_font_dir(given: str | None) -> Path:
    candidates = [Path(given)] if given else []
    try:
        import matplotlib

        candidates.append(Path(matplotlib.get_data_path()) / "fonts" / "ttf")
    except ImportError:
        pass
    candidates += [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu")]
    for directory in candidates:
        if (directory / "DejaVuSansMono.ttf").exists():
            return directory
    sys.exit(
        "DejaVu Sans Mono not found. Run with `uv run --with matplotlib python "
        "scripts/screenshots.py`, or pass --font-dir."
    )


class Fonts:
    """The four DejaVu Sans Mono faces, and fallbacks for glyphs it lacks."""

    FILES = {
        (False, False): "DejaVuSansMono.ttf",
        (True, False): "DejaVuSansMono-Bold.ttf",
        (False, True): "DejaVuSansMono-Oblique.ttf",
        (True, True): "DejaVuSansMono-BoldOblique.ttf",
    }
    FALLBACKS = (
        "/usr/share/fonts/Adwaita/AdwaitaMono-Regular.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Apple Symbols.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    )

    def __init__(self, directory: Path) -> None:
        self.faces = {
            key: ImageFont.truetype(str(directory / name), FONT_PX)
            for key, name in self.FILES.items()
        }
        self.fallbacks = []
        for path in self.FALLBACKS:
            try:
                self.fallbacks.append(ImageFont.truetype(path, FONT_PX))
            except OSError:
                continue
        ascent, descent = self.faces[(False, False)].getmetrics()
        self.baseline = round((CELL_H - (ascent + descent)) / 2 + ascent)
        self._has: dict[tuple[int, str], bool] = {}

    def has(self, face: ImageFont.FreeTypeFont, ch: str) -> bool:
        key = (id(face), ch)
        if key not in self._has:
            # A missing glyph draws the font's "notdef" box: same as U+FFFF.
            self._has[key] = self._ink(face, ch) != self._ink(face, chr(0xFFFF))
        return self._has[key]

    @staticmethod
    def _ink(face: ImageFont.FreeTypeFont, ch: str) -> bytes:
        probe = Image.new("L", (CELL_W * 2, CELL_H))
        ImageDraw.Draw(probe).text((0, CELL_H - 8), ch, font=face, fill=255, anchor="ls")
        return probe.tobytes()

    def supports(self, text: str) -> bool:
        """Whether every character of ``text`` can be drawn."""
        return all(
            ch == " " or cell_len(ch) == 0 or ch in DRAWN or self.pick(ch, False, False)
            for ch in text
        )

    def pick(self, ch: str, bold: bool, italic: bool) -> ImageFont.FreeTypeFont | None:
        face = self.faces[(bold, italic)]
        if self.has(face, ch):
            return face
        return next((f for f in self.fallbacks if self.has(f, ch)), None)


def blend(a: RGB, b: RGB, t: float) -> RGB:
    """``a`` moved ``t`` of the way to ``b``."""
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def triplet(color: RichColor | None, default: RGB) -> RGB:
    if color is None or color.is_default:
        return default
    t = color.get_truecolor(DEFAULT_TERMINAL_THEME)
    return (t.red, t.green, t.blue)


_LIGHT, _HEAVY = 2, 4
# Drawn by draw_block_or_line, not typeset.
DRAWN = set("█▀▄▌▐░▒▓─━│┃╭╮╰╯") | {chr(c) for c in (*range(0x2581, 0x2588), *range(0x2589, 0x2590))}


def draw_block_or_line(d: ImageDraw.ImageDraw, ch: str, x: int, y: int, fg: RGB, bg: RGB) -> bool:
    """Block elements and box lines are drawn, not typeset: a font's glyph
    stops short of the cell's edges (dashed borders, gaps in the bar), a
    terminal's don't. True if ``ch`` was one of them."""
    right, bottom = x + CELL_W, y + CELL_H
    cx, cy = x + CELL_W // 2, y + CELL_H // 2

    def fill(x0: float, y0: float, x1: float, y1: float, colour: RGB = fg) -> None:
        d.rectangle((round(x0), round(y0), round(x1) - 1, round(y1) - 1), fill=colour)

    code = ord(ch)
    if ch == "█":
        fill(x, y, right, bottom)
    elif ch == "▀":
        fill(x, y, right, y + CELL_H / 2)
    elif ch == "▄":
        fill(x, y + CELL_H / 2, right, bottom)
    elif 0x2581 <= code <= 0x2587:  # lower 1/8 .. 7/8
        fill(x, bottom - CELL_H * (code - 0x2580) / 8, right, bottom)
    elif 0x2589 <= code <= 0x258F:  # left 7/8 .. 1/8
        fill(x, y, x + CELL_W * (0x2590 - code) / 8, bottom)
    elif ch == "▌":
        fill(x, y, x + CELL_W / 2, bottom)
    elif ch == "▐":
        fill(x + CELL_W / 2, y, right, bottom)
    elif ch in "░▒▓":
        fill(x, y, right, bottom, blend(bg, fg, {"░": 0.25, "▒": 0.5, "▓": 0.75}[ch]))
    elif ch in "─━":
        t = _HEAVY if ch == "━" else _LIGHT
        fill(x, cy - t / 2, right, cy + t / 2)
    elif ch in "│┃":
        t = _HEAVY if ch == "┃" else _LIGHT
        fill(cx - t / 2, y, cx + t / 2, bottom)
    elif ch in "╭╮╰╯":
        r = CELL_W // 2 - 1
        t = _LIGHT
        down = ch in "╭╮"
        east = ch in "╭╰"
        # The straight parts, then a quarter circle joining them.
        if east:
            fill(cx + r, cy - t / 2, right, cy + t / 2)
        else:
            fill(x, cy - t / 2, cx - r, cy + t / 2)
        if down:
            fill(cx - t / 2, cy + r, cx + t / 2, bottom)
        else:
            fill(cx - t / 2, y, cx + t / 2, cy - r)
        corner = {
            "╭": (cx, cy, 180),
            "╮": (cx - 2 * r, cy, 270),
            "╰": (cx, cy - 2 * r, 90),
            "╯": (cx - 2 * r, cy - 2 * r, 0),
        }[ch]
        d.arc(
            (corner[0], corner[1], corner[0] + 2 * r, corner[1] + 2 * r),
            corner[2],
            corner[2] + 90,
            fill=fg,
            width=t,
        )
    else:
        return False
    return True


def paint(strips, background: RGB, foreground: RGB, fonts: Fonts, missing: set[str]) -> Image.Image:
    """The screen's strips as an image, one cell at a time. Characters no font
    has go into ``missing`` (they're left blank)."""
    img = Image.new(
        "RGB", (max(s.cell_length for s in strips) * CELL_W, len(strips) * CELL_H), background
    )
    d = ImageDraw.Draw(img)
    for row, strip in enumerate(strips):
        y = row * CELL_H
        col = 0
        for seg in strip:
            if seg.control:
                continue
            style = seg.style
            fg = triplet(style.color if style else None, foreground)
            bg = triplet(style.bgcolor if style else None, background)
            if style and style.reverse:
                fg, bg = bg, fg
            if style and style.dim:
                fg = blend(fg, bg, 0.4)
            bold = bool(style and style.bold)
            italic = bool(style and style.italic)
            for ch in seg.text:
                width = cell_len(ch)
                if width == 0:
                    continue
                x = col * CELL_W
                if bg != background:
                    d.rectangle((x, y, x + width * CELL_W - 1, y + CELL_H - 1), fill=bg)
                if ch != " " and not draw_block_or_line(d, ch, x, y, fg, bg):
                    face = fonts.pick(ch, bold, italic)
                    if face is None:
                        missing.add(ch)
                    else:
                        d.text((x, y + fonts.baseline), ch, font=face, fill=fg, anchor="ls")
                if style and style.underline:
                    d.rectangle(
                        (x, y + CELL_H - 5, x + width * CELL_W - 1, y + CELL_H - 4), fill=fg
                    )
                col += width
    return img


def paste_covers(img: Image.Image, screen) -> None:
    """Each cover over its cells, stretched to the cell box (as the Kitty
    protocol does), cut at what's visible."""
    from qobit.ui.widgets.art import Art

    for widget, (region, clip) in screen._compositor.visible_widgets.items():
        if not isinstance(widget, Art) or widget.image is None:
            continue
        shown = region.intersection(clip)
        if not shown.area:
            continue
        cover = widget.image.convert("RGB").resize(
            (region.width * CELL_W, region.height * CELL_H), Image.LANCZOS
        )
        left = (shown.x - region.x) * CELL_W
        top = (shown.y - region.y) * CELL_H
        cover = cover.crop((left, top, left + shown.width * CELL_W, top + shown.height * CELL_H))
        img.paste(cover, (shown.x * CELL_W, shown.y * CELL_H))


# ── the app ──────────────────────────────────────────────────────────────────


def isolate(tmp: Path) -> None:
    """Point every qobit path at ``tmp`` (settings, queue, covers, library)."""
    from qobit import config, library_cache, store
    from qobit.lyrics import cache as lyrics_cache
    from qobit.ui import _images

    config.CONFIG_DIR = tmp / "config"
    config.CONFIG_FILE = tmp / "config" / "config.json"
    store.DATA_DIR = tmp / "data"
    _images._CACHE_DIR = tmp / "cache"
    library_cache.DIR = tmp / "library"
    lyrics_cache.DIR = tmp / "lyrics"


def fix_cell_size() -> None:
    """The size the pictures are drawn at, so squares come out square."""
    from textual_image._terminal import CellSize
    from textual_image.widget import _base

    from qobit.ui.widgets import art

    size = CellSize(CELL_W, CELL_H)
    art.get_cell_size = lambda: size
    _base.get_cell_size = lambda: size


class _NoMediaKeys:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __getattr__(self, name: str):
        return lambda *a, **k: None


async def settle(app, pilot: Pilot, quiet: float = 0.8, timeout: float = 60.0) -> None:
    """Until every worker (lists, pages, covers) has been done for ``quiet`` s."""
    end = time.monotonic() + timeout
    idle_since = None
    while time.monotonic() < end:
        await pilot.pause(0.2)
        if any(w.is_running for w in app.workers):
            idle_since = None
        elif idle_since is None:
            idle_since = time.monotonic()
        elif time.monotonic() - idle_since >= quiet:
            return
    raise TimeoutError("the app didn't settle")


async def play(app, pilot: Pilot) -> None:
    """``NOW_PLAYING`` as the playing track: the real album, queue, bio and
    lyrics; the play state is set by hand (no mpv, no audio)."""
    from qobit.audio.verify import Signal
    from qobit.qobuz.models import Album, StreamUrl

    artist, title = NOW_PLAYING
    tracks = await app.ensure_favorite_tracks()
    mine = next((t for t in tracks if (t.artist, t.title) == (artist, title)), None)
    if mine is None:
        sys.exit(f"{artist} - {title} isn't one of your favourite tracks.")
    album = Album.from_api(await app._client.get_album(mine.album_id))
    now = next(t for t in album.tracks if t.id == mine.id)
    after = album.tracks[album.tracks.index(now) + 1 :]
    shelf = [a for a in await app.ensure_favorite_albums() if a.artist == artist]
    for other in shelf:  # the artist's other favourite albums follow
        if other.id != album.id:
            after += Album.from_api(await app._client.get_album(other.id)).tracks[:5]

    # Before it: a few of your favourite tracks, one per artist.
    before, seen = [], {artist}
    for t in tracks:
        if t.artist not in seen and len(before) < 5:
            before.append(t)
            seen.add(t.artist)

    bits = album.maximum_bit_depth or 16
    rate = (album.maximum_sampling_rate or 44.1) * 1000
    stream = StreamUrl("", 7 if bits > 16 else 6, rate, bits)
    app._timeline = [*before, now, *after]
    app._cursor = len(before)
    app.radio_mode = True
    app._last_stream = stream
    app.signal = Signal("ok", dac_checked=True)
    app.playback_dur = float(now.duration)
    app.playback_pos = round(now.duration * PLAYED_FRACTION)
    app.now_playing = now
    app.quality_label = stream.quality_badge
    app.is_playing = True
    app.is_paused = False
    app.queue_version += 1
    app._fetch_now_playing_album(now)  # a worker: the real album, then the bio
    await settle(app, pilot)


# ── the scenes ───────────────────────────────────────────────────────────────


def base_view(app, name: str):
    return app.screen_stack[0].query_one(f"#view-{name}")


async def tab(app, pilot: Pilot, name: str):
    app.action_switch_tab(name)
    await settle(app, pilot)
    return base_view(app, name)


async def scene_queue(app, pilot: Pilot, fonts: Fonts) -> None:
    view = await tab(app, pilot, "queue")
    # From the top: Recently Played, the playing row (the cursor), Up Next.
    view.query_one("#queue-list").scroll_home(animate=False)


async def scene_tracks(app, pilot: Pilot, fonts: Fonts) -> None:
    view = await tab(app, pilot, "tracks")
    lv = view.query_one("#fav-tracks")
    # A stretch of the list the fonts can draw all of (a Japanese title is a
    # gap here, and a terminal shows it): the cursor a few rows in.
    rows = [r for r in lv.rows if hasattr(r, "track")][:100]
    shown = max(1, lv.size.height // 2)  # two lines per track

    def drawable(row) -> bool:
        t = row.track
        return fonts.supports(f"{t.artist} {t.title} {t.album}")

    start = next(
        (i for i in range(len(rows) - shown) if all(drawable(r) for r in rows[i : i + shown])), 0
    )
    lv.index = start + 3
    lv.scroll_to(y=start * 2, animate=False)


async def scene_artists(app, pilot: Pilot, fonts: Fonts) -> None:
    await tab(app, pilot, "artists")


async def scene_artist(app, pilot: Pilot, fonts: Fonts) -> None:
    view = await tab(app, pilot, "artists")
    artists = await app.ensure_favorite_artists()
    artist = next((a for a in artists if a.name == ARTIST_PAGE), None) or artists[0]
    view._open_artist(artist)
    await settle(app, pilot)


async def scene_albums(app, pilot: Pilot, fonts: Fonts) -> None:
    view = base_view(app, "artists")
    if view._view != "grid":  # back from the artist page
        view.action_navigate_back()
    await tab(app, pilot, "albums")


async def scene_album(app, pilot: Pilot, fonts: Fonts) -> None:
    view = await tab(app, pilot, "albums")
    albums = await app.ensure_favorite_albums()
    album = next((a for a in albums if (a.artist, a.title) == ALBUM_PAGE), albums[0])
    view._open_album(album)
    await settle(app, pilot)


SCENES: dict[str, Callable[[object, Pilot, Fonts], Awaitable[None]]] = {
    "queue_page": scene_queue,
    "tracks_page": scene_tracks,
    "artists_page": scene_artists,
    "artist_view": scene_artist,
    "albums_page": scene_albums,
    "album_view": scene_album,
}


async def run(out: Path, only: list[str], font_dir: Path) -> None:
    from qobit import config
    from qobit.ui import app as app_module

    session = config.get_oauth_session()
    if not all(session):
        sys.exit("No saved Qobuz session. Log in first: qobit auth")

    with tempfile.TemporaryDirectory(prefix="qobit-shots-") as tmp:
        isolate(Path(tmp))
        fix_cell_size()
        app_module.MediaKeys = _NoMediaKeys
        app = app_module.QobitApp(connect=False, images="blocks")
        app._client.restore_session(*session)
        app._poll_player = lambda: None  # no mpv: the play state is set by hand
        fonts = Fonts(font_dir)
        out.mkdir(parents=True, exist_ok=True)

        async with app.run_test(size=(COLS, ROWS)) as pilot:
            await settle(app, pilot)
            await play(app, pilot)
            variables = app.theme_variables
            background = triplet(RichColor.parse(variables["background"]), (0, 0, 0))
            foreground = triplet(RichColor.parse(variables["foreground"]), (255, 255, 255))
            for name, scene in SCENES.items():
                if only and name not in only:
                    continue
                await pilot.resize_terminal(*SCENE_SIZE.get(name, (COLS, ROWS)))
                await settle(app, pilot)
                await scene(app, pilot, fonts)
                await pilot.pause(0.3)
                screen = app.screen
                missing: set[str] = set()
                strips = screen._compositor.render_strips()
                img = paint(strips, background, foreground, fonts, missing)
                paste_covers(img, screen)
                path = out / f"{name}.png"
                img.save(path, optimize=True)
                print(f"{path}  {img.width}x{img.height}")
                if missing:
                    print(f"  no glyph for: {' '.join(sorted(missing))} (left blank)")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Render the README screenshots.")
    parser.add_argument("--out", default=str(root / "screenshots"), help="output folder")
    parser.add_argument("--only", nargs="+", choices=list(SCENES), default=[], help="these only")
    parser.add_argument("--font-dir", help="folder with DejaVuSansMono*.ttf")
    args = parser.parse_args()
    asyncio.run(run(Path(args.out), args.only, find_font_dir(args.font_dir)))


if __name__ == "__main__":
    main()
