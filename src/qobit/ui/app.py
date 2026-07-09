from __future__ import annotations

import asyncio
import dataclasses
import time
from functools import lru_cache

from rich.color import Color as _RichColor
from rich.color import ColorType as _ColorType
from rich.style import Style as _RichStyle
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.filter import NO_DIM, dim_color
from textual.filter import ANSIToTruecolor as _ANSIToTruecolor
from textual.reactive import reactive
from textual.widgets import ContentSwitcher, Footer, Label, ListView, Tab, Tabs
from textual.worker import get_current_worker

from ..audio.media_keys import MediaKeys
from ..audio.player import MpvPlayer
from ..config import (
    get_audio_device,
    get_oauth_session,
    get_radio_mode,
    get_saved_theme,
    get_transparent_background,
    set_radio_mode,
    set_saved_theme,
    set_transparent_background,
)
from ..qobuz.client import AuthExpiredError, QobuzClient, QobuzError
from ..qobuz.models import Album, Artist, StreamUrl, Track
from ..store import load as load_state
from ..store import save as save_state
from .screens.albums import AlbumsView
from .screens.artists import ArtistsView
from .screens.mixes import MixesView
from .screens.playlists import PlaylistsView
from .screens.queue import QueueView
from .screens.search import SearchView
from .screens.tracks import TracksView
from .widgets.transport import TransportBar


class _TransparentANSIToTruecolor(_ANSIToTruecolor):
    """ANSIToTruecolor that preserves ansi_default backgrounds so the
    terminal's own background (transparency/blur) shows through."""

    @lru_cache(1024)
    def truecolor_style(self, style: "_RichStyle", background: "_RichColor") -> "_RichStyle":
        terminal_theme = self._terminal_theme
        changed = False

        color = style.color
        if color is not None and color.triplet is None:
            color = _RichColor.from_triplet(color.get_truecolor(terminal_theme, foreground=True))
            changed = True

        bgcolor = style.bgcolor
        keep_default_bg = bgcolor is not None and bgcolor.type == _ColorType.DEFAULT
        if bgcolor is not None and bgcolor.triplet is None and not keep_default_bg:
            bgcolor = _RichColor.from_triplet(
                bgcolor.get_truecolor(terminal_theme, foreground=False)
            )
            changed = True

        if style.dim and color is not None:
            if bgcolor is not None and bgcolor.triplet is not None:
                dim_bg = bgcolor
            elif background.triplet is not None:
                dim_bg = background
            else:
                dim_bg = _RichColor.from_triplet(
                    _RichColor.default().get_truecolor(terminal_theme, foreground=False)
                )
            color = dim_color(dim_bg, color)
            style += NO_DIM
            changed = True

        return style + _RichStyle.from_color(color, bgcolor) if changed else style


class QobitCommands(Provider):
    def _commands(self) -> list[tuple[str, object, str]]:
        app: QobitApp = self.app  # type: ignore[assignment]
        return [
            (
                "Draw theme background",
                app.action_toggle_background,
                "Toggle between theme background and terminal transparency",
            ),
            (
                f"Endless radio: turn {'off' if app.radio_mode else 'on'}",
                app.action_toggle_radio_mode,
                "Auto-fill Up Next with Qobuz song-radio suggestions when the queue runs dry",
            ),
        ]

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for label, callback, help_text in self._commands():
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), callback, help=help_text)

    async def discover(self) -> Hits:
        for label, callback, help_text in self._commands():
            yield Hit(0, label, callback, help=help_text)


_PLAYER_STATE_FILE = "player_state.json"

# Most recently-played tracks kept in the session history (oldest dropped first).
_HISTORY_MAX = 100
# Pressing "previous" past this many seconds into a track restarts it; before
# it, steps back to the previous track in history (standard media convention).
_PREV_RESTART_THRESHOLD = 3.0

_TABS = [
    ("tracks", "Tracks"),
    ("artists", "Artists"),
    ("albums", "Albums"),
    ("playlists", "Playlists"),
    ("mixes", "Mixes"),
    ("search", "Search"),
    ("queue", "Queue"),
]


class QobitApp(App[None]):
    TITLE = "qobit"
    COMMANDS = App.COMMANDS | {QobitCommands}
    CSS = """
    Screen {
        layout: vertical;
    }
    /* Unified scrollbar design across every scrollable widget / page. */
    * {
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
        scrollbar-background: $surface;
        scrollbar-background-hover: $surface;
        scrollbar-background-active: $surface;
        scrollbar-color: $surface-lighten-2;
        scrollbar-color-hover: $surface-lighten-2;
        scrollbar-color-active: $accent;
    }
    Tabs {
        dock: top;
    }
    ContentSwitcher {
        height: 1fr;
    }
    SearchView, PlaylistsView, TracksView, ArtistsView, AlbumsView, QueueView {
        height: 1fr;
        margin: 0 1 1 1;
    }
    TransportBar {
        margin: 0 1;
    }
    ListItem.--highlight {
        background: $accent 35%;
    }
    ListItem.--highlight > Label {
        background: $accent 35%;
    }
    Label.-hl {
        color: $accent;
    }
    Screen.-transparent,
    Screen.-transparent * {
        background: ansi_default;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "pause", "Pause"),
        Binding("[", "seek_back", "◀10s"),
        Binding("]", "seek_fwd", "10s▶"),
        Binding("R", "start_radio", "Radio"),
        Binding("i", "track_menu", "Menu", show=False),
        # 1-5 work when Tabs has focus; escape brings focus to Tabs from anywhere
        Binding("1", "switch_tab('tracks')", "Tracks", show=False),
        Binding("2", "switch_tab('artists')", "Artists", show=False),
        Binding("3", "switch_tab('albums')", "Albums", show=False),
        Binding("4", "switch_tab('playlists')", "Playlists", show=False),
        Binding("5", "switch_tab('search')", "Search", show=False),
        Binding("6", "switch_tab('queue')", "Queue", show=False),
        # priority=True so this fires even when an Input has focus
        Binding("escape", "focus_tabs", "Nav", show=False, priority=True),
    ]

    now_playing: reactive[Track | None] = reactive(None)
    # Full album of the now-playing track (year/genre/label/hi-res). Fetched
    # async by _fetch_now_playing_album so the Now Playing block can show rich
    # metadata the lean Track model doesn't carry. None until it arrives.
    now_playing_album: reactive[Album | None] = reactive(None)
    # Biography of the now-playing artist, fetched async by _fetch_now_playing_bio
    # so the Queue's Now Playing hero can fill its space with the artist story.
    now_playing_bio: reactive[str] = reactive("")
    is_playing: reactive[bool] = reactive(False)
    is_paused: reactive[bool] = reactive(False)
    playback_pos: reactive[float] = reactive(0.0)
    playback_dur: reactive[float] = reactive(0.0)
    status_msg: reactive[str] = reactive("")
    queue_version: reactive[int] = reactive(0)
    quality_label: reactive[str] = reactive("")
    # Endless radio: when on, _advance_queue refills an empty queue with Qobuz
    # song-radio suggestions. Reactive so the transport indicator stays live.
    radio_mode: reactive[bool] = reactive(False)

    def __init__(self) -> None:
        super().__init__()
        self._client = QobuzClient()
        self._player = MpvPlayer(audio_device=get_audio_device())
        self._play_queue: list[Track] = []
        # Tracks played earlier this session, oldest first. The just-finished
        # track is appended on each track change (see _do_play). Session-only —
        # not persisted. Capped at _HISTORY_MAX to bound memory.
        self._history: list[Track] = []
        # A restored-but-not-yet-loaded track: (track, position). Set on launch
        # from the persisted player state; resumed on the next play/pause press.
        self._pending_resume: tuple[Track, float] | None = None
        self._restoring: bool = False
        self._last_state_save: float = 0.0
        # Favourite tracks, fetched once and shared by TracksView (full list)
        # and the heart glyph in other track lists (id set). None = not loaded.
        # Kept in sync by toggle_favorite so all views stay current.
        self._fav_tracks: list[Track] | None = None
        self._fav_ids: set[str] | None = None
        self._fav_lock = asyncio.Lock()
        # Prevents two concurrent _advance_queue calls from both seeing an empty
        # queue and both fetching radio + starting playback.
        self._advance_lock = asyncio.Lock()
        # Maps track_id → last successful stream quality so _do_play tries the
        # known-good format first instead of always starting at FLAC_24_192.
        self._track_quality_cache: dict[str, str] = {}
        self._flash_timer: object | None = None
        self._media_keys = MediaKeys(
            on_play_pause=lambda: self.call_from_thread(self.action_pause),
            on_next=lambda: self.call_from_thread(self._advance_queue),
            on_previous=lambda: self.call_from_thread(self.action_previous),
        )
        app_id, token, secrets = get_oauth_session()
        if app_id and token and secrets:
            self._client.restore_session(app_id, token, secrets)

    def compose(self) -> ComposeResult:
        yield Tabs(
            *[Tab(label, id=tid) for tid, label in _TABS],
            id="nav-tabs",
            active="tracks",
        )
        with ContentSwitcher(initial="view-tracks"):
            yield PlaylistsView(id="view-playlists")
            yield TracksView(id="view-tracks")
            yield ArtistsView(id="view-artists")
            yield AlbumsView(id="view-albums")
            yield MixesView(id="view-mixes")
            yield SearchView(id="view-search")
            yield QueueView(id="view-queue")
        yield TransportBar()
        yield Footer()

    async def on_mount(self) -> None:
        self._poll_player()
        saved_theme = get_saved_theme()
        if saved_theme and saved_theme in self.available_themes:
            self.theme = saved_theme
        # Persist any subsequent theme changes (skip the initial/default ones).
        self._theme_ready = True
        if get_transparent_background():
            self.action_toggle_background()
        self.radio_mode = get_radio_mode()
        self._restore_player_state()
        self._warm_favorite_ids()
        self._prune_image_cache()

    @work(thread=True)
    def _prune_image_cache(self) -> None:
        from ._images import prune_disk_cache

        prune_disk_cache()

    def watch_theme(self, theme: str) -> None:
        if getattr(self, "_theme_ready", False):
            set_saved_theme(theme)

    # ── tab switching ─────────────────────────────────────────────────────────

    @on(Tabs.TabActivated, "#nav-tabs")
    def _on_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None:
            return
        self.query_one(ContentSwitcher).current = f"view-{event.tab.id}"
        if event.tab.id == "search":
            self._focus_search_input()

    def action_switch_tab(self, tab_id: str) -> None:
        self.query_one("#nav-tabs", Tabs).active = tab_id

    def action_focus_tabs(self) -> None:
        if len(self.screen_stack) > 1:
            screen = self.screen
            if hasattr(screen, "action_navigate_back"):
                screen.action_navigate_back()
            else:
                self.pop_screen()
        else:
            cs = self.query_one(ContentSwitcher)
            if cs.current:
                try:
                    active = self.query_one(f"#{cs.current}")
                    if hasattr(active, "action_navigate_back"):
                        if active.action_navigate_back():
                            return
                except Exception:
                    pass
            self.query_one("#nav-tabs", Tabs).focus()

    _hl_item: object = None

    @on(ListView.Highlighted)
    def _on_list_highlighted(self, event: ListView.Highlighted) -> None:
        # Only touch the previously highlighted row, not every row in the list —
        # a full query(Label) over a long list on each arrow press is what made
        # the track list feel laggy.
        prev = self._hl_item
        if prev is not None and prev is not event.item:
            for label in prev.query(Label):
                label.remove_class("-hl")
        if event.item:
            for label in event.item.query(Label):
                label.add_class("-hl")
        self._hl_item = event.item

    def _focus_search_input(self) -> None:
        try:
            self.query_one("#search-input").focus()
        except Exception:
            pass

    # ── favourite tracks (heart glyph in track lists) ─────────────────────────

    async def ensure_favorite_tracks(self) -> list[Track]:
        """Favourite tracks, fetched exactly once and cached as `Track`s.

        Double-checked locking serialises concurrent first callers (TracksView's
        list load, the mount-time heart warm, the QueueView refresh, each
        track-list builder) so the paginated favourites endpoint is hit once.
        Raises on fetch failure (without caching) so callers can retry."""
        if self._fav_tracks is not None:
            return self._fav_tracks
        async with self._fav_lock:
            if self._fav_tracks is None:
                raw = await self._client.get_all_favorite_tracks()
                self._fav_tracks = [Track.from_api(r) for r in raw]
            return self._fav_tracks

    async def ensure_favorite_ids(self) -> set[str]:
        """Set of favourited track ids, derived from the shared favourites
        cache. Degrades to an empty set if the fetch fails (no hearts)."""
        if self._fav_ids is None:
            try:
                tracks = await self.ensure_favorite_tracks()
            except Exception:
                tracks = []
            self._fav_ids = {t.id for t in tracks}
        return self._fav_ids

    async def toggle_favorite(self, track: Track) -> bool:
        """Add/remove a track from the user's Qobuz favourites, keep the shared
        caches in sync, and refresh the Tracks tab. Returns the new favourite
        state (unchanged on API error)."""
        tid = str(track.id)
        ids = await self.ensure_favorite_ids()  # also loads _fav_tracks
        try:
            if tid in ids:
                await self._client.remove_favorite_track(tid)
                ids.discard(tid)
                if self._fav_tracks is not None:
                    self._fav_tracks = [t for t in self._fav_tracks if t.id != tid]
                new_state = False
            else:
                await self._client.add_favorite_track(tid)
                ids.add(tid)
                if self._fav_tracks is not None and not any(t.id == tid for t in self._fav_tracks):
                    added = dataclasses.replace(track, favorited_at=int(time.time()))
                    self._fav_tracks.append(added)
                new_state = True
        except Exception:
            self.status_msg = "Couldn't update favourite"
            return tid in ids
        try:
            self.query_one(TracksView).favorite_changed(tid, new_state)
        except Exception:
            pass
        # Refresh the Queue so hearts on history/now-playing/up-next rows update.
        self.queue_version += 1
        return new_state

    @work
    async def _warm_favorite_ids(self) -> None:
        await self.ensure_favorite_ids()

    # ── seek from mouse click on bar ─────────────────────────────────────────

    def on_transport_bar_seek_to(self, event: TransportBar.SeekTo) -> None:
        self._player.seek_to(event.position)

    # ── player poll (thread — IPC blocks) ────────────────────────────────────

    @work(thread=True)
    def _poll_player(self) -> None:
        worker = get_current_worker()
        _seen_gen: int = 0
        while not worker.is_cancelled:
            if self._player.running:
                _seen_gen = self._player._stop_gen
                pos = self._player.get_property("time-pos")
                dur = self._player.get_property("duration")
                paused = self._player.get_property("pause")
                if pos is not None:
                    self.call_from_thread(setattr, self, "playback_pos", float(pos))
                if dur is not None:
                    self.call_from_thread(setattr, self, "playback_dur", float(dur))
                if paused is not None:
                    self.call_from_thread(setattr, self, "is_paused", bool(paused))
                if not self.is_playing:
                    self.call_from_thread(setattr, self, "is_playing", True)
                # Persist position periodically so a crash/kill loses ≤5s.
                now = time.monotonic()
                if now - self._last_state_save > 5.0:
                    self._last_state_save = now
                    self.call_from_thread(self._save_player_state)
            elif self.is_playing:
                natural_end = self._player._stop_gen == _seen_gen
                self.call_from_thread(setattr, self, "is_playing", False)
                self.call_from_thread(setattr, self, "is_paused", False)
                self.call_from_thread(setattr, self, "playback_pos", 0.0)
                self.call_from_thread(setattr, self, "quality_label", "")
                if natural_end:
                    self.call_from_thread(self._advance_queue)
            time.sleep(0.5)

    # ── play (async worker — shares event loop with httpx) ───────────────────

    @work
    async def play_track(
        self, track: Track, queue: list[Track] | None = None, start: float = 0.0
    ) -> None:
        self._pending_resume = None
        if queue is not None:
            self._play_queue = list(queue)
            self.queue_version += 1
        await self._do_play(track, start=start)

    def clear_queue(self) -> None:
        if self._play_queue:
            self._play_queue.clear()
            self.queue_version += 1

    def clear_history(self) -> None:
        if self._history:
            self._history.clear()
            self.queue_version += 1

    def queue_next(self, track: Track) -> None:
        """Insert a track at the head of Up Next so it plays immediately after
        the current one."""
        self._play_queue.insert(0, track)
        self.queue_version += 1

    def queue_last(self, track: Track) -> None:
        """Append a track to the tail of Up Next."""
        self._play_queue.append(track)
        self.queue_version += 1

    def remove_from_queue(self, track: Track) -> bool:
        """Drop a track from Up Next. Matches by identity first so the exact
        queued instance is removed even if the same track sits at several
        positions; falls back to the first id match. Returns whether it removed."""
        for i, t in enumerate(self._play_queue):
            if t is track:
                del self._play_queue[i]
                self.queue_version += 1
                return True
        for i, t in enumerate(self._play_queue):
            if t.id == track.id:
                del self._play_queue[i]
                self.queue_version += 1
                return True
        return False

    @work
    async def _advance_queue(self) -> None:
        async with self._advance_lock:
            if not self._play_queue and self.radio_mode and self.now_playing is not None:
                # Queue ran dry under endless radio: refill from suggestions seeded
                # by the just-finished track (now_playing is still that track here).
                suggestions = await self._fetch_radio(self.now_playing)
                if suggestions:
                    self._play_queue = suggestions
                    self.queue_version += 1
            if not self._play_queue:
                return
            next_track = self._play_queue.pop(0)
            self.queue_version += 1
            await self._do_play(next_track)

    # ── song radio (dynamic/suggest) ─────────────────────────────────────────

    def _flash_status(self, msg: str, secs: float = 3.0) -> None:
        """Show a transient status message, then revert. status_msg drives the
        transport's main label (TransportBar._on_status_msg), so a *persistent*
        message would hide the artist — title until the next track loads. Auto-
        clearing lets the player fall back to the now-playing track."""
        if self._flash_timer is not None:
            self._flash_timer.stop()  # type: ignore[union-attr]
        self.status_msg = msg
        self._flash_timer = self.set_timer(secs, lambda: self._clear_flash(msg))

    def _clear_flash(self, msg: str) -> None:
        if self.status_msg == msg:  # only clear if nothing newer replaced it
            self.status_msg = ""

    def action_start_radio(self) -> None:
        seed = self.now_playing or (self._pending_resume[0] if self._pending_resume else None)
        if seed is None:
            self._flash_status("Play something to start radio")
            return
        # No pre-message: keep the current track visible in the player while the
        # suggestions load; _start_radio flashes the result.
        self._start_radio(seed)

    @work
    async def _start_radio(self, seed: Track) -> None:
        suggestions = await self._fetch_radio(seed)
        if not suggestions:
            self._flash_status("No radio suggestions available")
            return
        # Replace Up Next with the fresh station.
        self._play_queue = suggestions
        self.queue_version += 1
        self._flash_status(f"Radio: {len(suggestions)} tracks queued")

    def action_toggle_radio_mode(self) -> None:
        self.radio_mode = not self.radio_mode
        set_radio_mode(self.radio_mode)
        self._flash_status(f"Endless radio {'on' if self.radio_mode else 'off'}")

    @staticmethod
    def _as_int(value: object) -> int | None:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    async def _analysed_entry(self, seed: Track) -> dict | None:
        """Build one `track_to_analysed` entry (artist/label/genre ids) for the
        seed. Reuses the already-fetched now_playing_album when it matches;
        otherwise pulls the ids from a single track/get."""
        artist_id = label_id = genre_id = None
        album = self.now_playing_album
        if album is not None and self.now_playing is not None and self.now_playing.id == seed.id:
            artist_id, label_id, genre_id = album.artist_id, album.label_id, album.genre_id
        else:
            try:
                data = await self._client.get_track(str(seed.id))
            except QobuzError:
                return None
            alb = data.get("album") or {}
            artist = alb.get("artist") or data.get("performer") or {}
            artist_id = artist.get("id")
            label_id = (alb.get("label") or {}).get("id")
            genre_id = (alb.get("genre") or {}).get("id")
        artist_id, label_id, genre_id = (
            self._as_int(artist_id),
            self._as_int(label_id),
            self._as_int(genre_id),
        )
        if artist_id is None and genre_id is None:
            return None
        return {
            "track_id": self._as_int(seed.id),
            "artist_id": artist_id,
            "label_id": label_id,
            "genre_id": genre_id,
        }

    async def _fetch_radio(self, seed: Track) -> list[Track]:
        """Fetch song-radio suggestions seeded from `seed` + recent history.
        Returns parsed Tracks with the seed window de-duplicated out. Best-effort:
        any failure yields an empty list and the caller leaves the queue intact."""
        listened: list[int] = []
        for track in [seed, *reversed(self._history)]:
            tid = self._as_int(track.id)
            if tid is not None and tid not in listened:
                listened.append(tid)
            if len(listened) >= 10:
                break

        entry = await self._analysed_entry(seed)
        try:
            resp = await self._client.get_dynamic_suggestions(listened, [entry] if entry else [])
        except QobuzError:
            return []

        seen = {str(i) for i in listened}
        out: list[Track] = []
        for item in (resp.get("tracks") or {}).get("items") or []:
            try:
                track = Track.from_api(item)
            except Exception:
                continue
            if str(track.id) in seen:
                continue
            seen.add(str(track.id))
            out.append(track)
        return out

    async def generate_mix(self, kind: str) -> list[Track]:
        """Generate a DailyQ / WeeklyQ from history + favourites via dynamic/suggest.

        Seeds up to 50 tracks from recent history (most-recent-first) topped up
        with favourites.  A spread of up to 12 seeds is enriched with
        artist/label/genre metadata for the `track_to_analysed` payload.  Returns
        an empty list when there are no seeds or the API fails."""
        fav_tracks: list[Track] = []
        try:
            fav_tracks = await self.ensure_favorite_tracks()
        except Exception:
            pass

        seen: set[str] = set()
        seeds: list[Track] = []
        for t in [*reversed(self._history), *fav_tracks]:
            if t.id not in seen:
                seen.add(t.id)
                seeds.append(t)
            if len(seeds) >= 50:
                break

        if not seeds:
            return []

        listened: list[int] = [tid for t in seeds if (tid := self._as_int(t.id)) is not None]

        analyse_count = min(12, len(seeds))
        if analyse_count > 1:
            step = (len(seeds) - 1) / (analyse_count - 1)
            picks = [seeds[round(i * step)] for i in range(analyse_count)]
        else:
            picks = seeds[:analyse_count]

        analysed_results = await asyncio.gather(
            *[self._analysed_entry(t) for t in picks], return_exceptions=True
        )
        analysed = [e for e in analysed_results if isinstance(e, dict)]

        try:
            resp = await self._client.get_dynamic_suggestions(listened, analysed)
        except QobuzError:
            return []

        seed_ids = {str(i) for i in listened}
        out: list[Track] = []
        seen2: set[str] = set(seed_ids)
        for item in (resp.get("tracks") or {}).get("items") or []:
            try:
                track = Track.from_api(item)
            except Exception:
                continue
            if track.id in seen2:
                continue
            seen2.add(track.id)
            out.append(track)
        return out

    async def _do_play(self, track: Track, start: float = 0.0, record_history: bool = True) -> None:
        self.status_msg = f"Loading {track.display_title}…"
        stream: StreamUrl | None = None
        track_id = str(track.id)
        # Prefer the quality that worked last time for this track; fall through to
        # the full list on first play or if the cached quality fails.
        all_qualities = ["FLAC_24_192", "FLAC_24_96", "FLAC_CD"]
        cached = self._track_quality_cache.get(track_id)
        if cached:
            qualities = [cached] + [q for q in all_qualities if q != cached]
        else:
            qualities = all_qualities
        for quality in qualities:
            try:
                data = await self._client.get_streaming_url(track_id, quality)
                stream = StreamUrl.from_api(data)
                self._track_quality_cache[track_id] = quality
                if len(self._track_quality_cache) > 1000:
                    del self._track_quality_cache[next(iter(self._track_quality_cache))]
                break
            except AuthExpiredError:
                self.status_msg = "Session expired — run: qobit auth"
                return
            except QobuzError:
                continue

        if stream is None:
            self.status_msg = "No stream available"
            return

        # Push the outgoing track to history before it's replaced. Skipped when
        # stepping backwards (record_history=False) so previous/next don't
        # ping-pong. This is the single chokepoint for every track change.
        prev = self.now_playing
        if record_history and prev is not None and prev.id != track.id:
            if len(self._history) >= _HISTORY_MAX:
                del self._history[0]
            self._history.append(prev)
            self.queue_version += 1

        if self._player.running:
            self._player.stop()
        self._player.play(stream.url, start=start)
        self.now_playing = track
        self.now_playing_album = None
        self.now_playing_bio = ""
        self.quality_label = stream.quality_badge
        self.playback_pos = start
        self.playback_dur = 0.0
        self.is_playing = True
        self.is_paused = False
        self.status_msg = ""
        self._fetch_now_playing_album(track)

    @work
    async def _fetch_now_playing_album(self, track: Track) -> None:
        """Pull the full album for the now-playing track so the Queue's Now
        Playing block can show year/genre/label/hi-res. Best-effort: a failure
        just leaves the extra metadata blank."""
        if not track.album_id:
            return
        try:
            data = await self._client.get_album(track.album_id)
        except QobuzError:
            return
        # Bail if the track changed while the album was loading.
        if self.now_playing is None or self.now_playing.id != track.id:
            return
        album = Album.from_api(data)
        self.now_playing_album = album
        if album.artist_id:
            self._fetch_now_playing_bio(track, album.artist_id)

    @work
    async def _fetch_now_playing_bio(self, track: Track, artist_id: str) -> None:
        """Pull the now-playing artist's biography for the Now Playing hero.
        Best-effort: a failure or missing bio just leaves the space empty.
        Uses albums_limit=1 since only the bio is needed."""
        try:
            data = await self._client.get_artist(artist_id, albums_limit=1)
        except QobuzError:
            return
        if self.now_playing is None or self.now_playing.id != track.id:
            return
        self.now_playing_bio = Artist.from_api(data).biography or ""

    # ── media key sync ───────────────────────────────────────────────────────

    def watch_now_playing(self, track: Track | None) -> None:
        self._media_keys.update(
            track, self.is_playing, self.is_paused, self.playback_pos, self.playback_dur
        )
        if track and track.image_url:
            self._fetch_media_key_art(track.image_url)
        self._save_player_state()

    # ── player state persistence (survive restarts) ──────────────────────────

    def _save_player_state(self) -> None:
        if self._restoring:
            return
        track = self.now_playing
        if track is None:
            return
        try:
            save_state(
                _PLAYER_STATE_FILE,
                {
                    "track": dataclasses.asdict(track),
                    "position": float(self.playback_pos),
                    "duration": float(self.playback_dur),
                },
            )
        except Exception:
            pass

    def _restore_player_state(self) -> None:
        """Reload the last track + position into the transport bar (paused,
        unloaded). The next play/pause press resumes it from that position."""
        data = load_state(_PLAYER_STATE_FILE)
        raw = data.get("track")
        if not isinstance(raw, dict):
            return
        try:
            track = Track(**raw)
        except (TypeError, ValueError):
            return
        position = float(data.get("position") or 0.0)
        duration = float(data.get("duration") or track.duration or 0.0)
        self._restoring = True
        self.playback_dur = duration
        self.playback_pos = position
        self.is_paused = True
        self.now_playing = track
        self._restoring = False
        self._pending_resume = (track, position)

    @work
    async def _fetch_media_key_art(self, url: str) -> None:
        from ._images import fetch_image

        image = await fetch_image(url)
        if image is not None:
            self._media_keys.set_artwork(image)

    def watch_is_playing(self, playing: bool) -> None:
        self._media_keys.update(
            self.now_playing, playing, self.is_paused, self.playback_pos, self.playback_dur
        )

    def watch_is_paused(self, paused: bool) -> None:
        self._media_keys.update(
            self.now_playing, self.is_playing, paused, self.playback_pos, self.playback_dur
        )

    # ── actions ──────────────────────────────────────────────────────────────

    def action_pause(self) -> None:
        if self._player.running:
            self._player.pause_toggle()
        elif self._pending_resume is not None:
            # Resume the track restored from the last session, from where it left off.
            track, position = self._pending_resume
            self.play_track(track, start=position)

    def action_previous(self) -> None:
        # Within the first few seconds, step back to the previous track in
        # history; otherwise (or with no history) restart the current track.
        if (
            self._player.running and self.playback_pos > _PREV_RESTART_THRESHOLD
        ) or not self._history:
            self._player.seek_to(0.0)
            return
        prev = self._history.pop()
        self.queue_version += 1
        # record_history=False so the track we just left isn't pushed back onto
        # history (which would make the next "previous" press ping-pong).
        self._play_previous(prev)

    @work
    async def _play_previous(self, track: Track) -> None:
        await self._do_play(track, record_history=False)

    # ── track context menu ───────────────────────────────────────────────────

    def action_track_menu(self) -> None:
        """Open the context menu for the track highlighted in the focused list.
        No-op unless a ListView with a track row currently holds focus."""
        from .screens.context_menu import TrackContextMenu
        from .screens.queue import QueueTrackRow

        focused = self.focused
        if not isinstance(focused, ListView):
            return
        child = focused.highlighted_child
        if child is None or not hasattr(child, "track"):
            return
        track: Track = child.track
        # "Remove from queue" only makes sense for an Up Next row.
        in_queue = isinstance(child, QueueTrackRow)
        self.push_screen(
            TrackContextMenu(track, in_queue=in_queue),
            lambda action: self._on_track_menu(action, track),
        )

    def _on_track_menu(self, action: str | None, track: Track) -> None:
        if action == "remove_queue":
            if self.remove_from_queue(track):
                self._flash_status(f"Removed from queue: {track.display_title}")
        elif action == "play_next":
            self.queue_next(track)
            self._flash_status(f"Playing next: {track.display_title}")
        elif action == "add_queue":
            self.queue_last(track)
            self._flash_status(f"Added to queue: {track.display_title}")
        elif action == "radio":
            self.play_track(track)
            self._start_radio(track)
        elif action == "artist":
            self._open_artist(track)
        elif action == "album":
            self._open_album(track)

    def _open_album(self, track: Track) -> None:
        from .screens.album_detail import AlbumScreen

        if not track.album_id:
            self._flash_status("No album for this track")
            return
        album = Album(
            id=track.album_id,
            title=track.album,
            artist=track.artist,
            year=None,
            tracks_count=0,
            image_url=track.image_url,
        )
        self.push_screen(AlbumScreen(album, source="Back"))

    @work
    async def _open_artist(self, track: Track) -> None:
        from .screens.artist_detail import ArtistScreen

        try:
            data = await self._client.get_track(str(track.id))
        except QobuzError:
            self._flash_status("Couldn't open artist")
            return
        album = data.get("album") or {}
        artist = album.get("artist") or data.get("performer") or {}
        artist_id = artist.get("id")
        if not artist_id:
            self._flash_status("Artist not available")
            return
        self.push_screen(ArtistScreen(str(artist_id), source="Back"))

    def action_seek_back(self) -> None:
        self._player.seek(-10.0)

    def action_seek_fwd(self) -> None:
        self._player.seek(10.0)

    def action_focus_search(self) -> None:
        self.query_one(Tabs).active = "search"

    def _refresh_truecolor_filter(self, theme: object) -> None:
        if not getattr(self, "_transparent", False) or self.native_ansi_color:
            return super()._refresh_truecolor_filter(theme)  # type: ignore[misc]
        for index, flt in enumerate(self._filters):
            if isinstance(flt, _ANSIToTruecolor):
                self._filters[index] = _TransparentANSIToTruecolor(theme, enabled=True)
                return

    def action_toggle_background(self) -> None:
        self._transparent = not getattr(self, "_transparent", False)
        for screen in self.screen_stack:
            screen.set_class(self._transparent, "-transparent")
        for index, flt in enumerate(self._filters):
            if isinstance(flt, (_ANSIToTruecolor, _TransparentANSIToTruecolor)):
                theme = flt._terminal_theme
                if self._transparent:
                    self._filters[index] = _TransparentANSIToTruecolor(theme, enabled=True)
                else:
                    self._filters[index] = _ANSIToTruecolor(theme, enabled=True)
                break
        set_transparent_background(self._transparent)
        self.refresh(layout=True)

    async def on_unmount(self) -> None:
        from ._images import close_client

        self._save_player_state()
        self._media_keys.close()
        self._player.stop()
        await self._client.close()
        await close_client()
