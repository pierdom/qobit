from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Track
from ..widgets.lists import TrackListView
from .search import ICON_TRACK

if TYPE_CHECKING:
    from ..app import QobitApp

_SORT_OPTIONS: list[tuple[str, str]] = [
    ("favorited_at", "Date Added"),
    ("artist", "Artist"),
    ("title", "Title"),
    ("album", "Album"),
]
_SORT_KEYS = [k for k, _ in _SORT_OPTIONS]

_BATCH = 200  # rows per mount call; yields the event loop between batches


class FavTrackRow(ListItem):
    DEFAULT_CSS = """
    FavTrackRow { height: 2; padding: 0 0 0 1; }
    FavTrackRow Label { width: 1fr; }
    FavTrackRow .primary { text-style: bold; }
    FavTrackRow .secondary { color: $text-muted; }
    """

    def __init__(self, track: Track) -> None:
        super().__init__()
        self.track = track

    def compose(self) -> ComposeResult:
        t = self.track
        yield Label(f"{ICON_TRACK}  {t.artist} — {t.display_title}", classes="primary")
        yield Label(f"     {t.album}  ·  {t.duration_str}", classes="secondary")


class TracksView(Widget):
    BINDINGS = [
        Binding("s", "cycle_sort", "Sort"),
        Binding("r", "toggle_reverse", "Rev"),
        Binding("/", "start_filter", "Filter"),
    ]

    DEFAULT_CSS = """
    TracksView {
        height: 1fr;
        layout: vertical;
    }
    TracksView #tracks-container {
        height: 1fr;
        layout: vertical;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
        border-subtitle-color: $accent 40%;
        border-subtitle-align: right;
        margin: 0;
    }
    TracksView #tracks-container.-focused {
        border: round $accent;
        border-title-color: $accent;
        border-subtitle-color: $accent;
    }
    TracksView ListView {
        height: 1fr;
        border: none;
    }
    """

    _loaded: bool = False
    _sort_key: str = "favorited_at"
    _sort_reverse: bool = True
    _filter_active: bool = False
    _filter_query: str = ""
    _render_version: int = 0
    _tracks: list[Track]
    _filter_timer: Timer | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._tracks = []

    def compose(self) -> ComposeResult:
        with Vertical(id="tracks-container"):
            yield TrackListView(id="fav-tracks")

    def on_mount(self) -> None:
        container = self.query_one("#tracks-container", Vertical)
        container.border_title = "Favourite Tracks"
        self._update_subtitle()

    def on_show(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._load()
        self.call_after_refresh(self.query_one("#fav-tracks", ListView).focus)

    def on_descendant_focus(self, _: events.DescendantFocus) -> None:
        self.query_one("#tracks-container", Vertical).add_class("-focused")

    def on_descendant_blur(self, _: events.DescendantBlur) -> None:
        self.query_one("#tracks-container", Vertical).remove_class("-focused")

    # ── filter ───────────────────────────────────────────────────────────────

    def action_navigate_back(self) -> bool:
        if self._filter_active or self._filter_query:
            self._filter_active = False
            self._filter_query = ""
            self._update_subtitle()
            self._render_list()
            return True
        return False

    def action_start_filter(self) -> None:
        self._filter_active = True
        self._update_subtitle()

    def on_key(self, event: events.Key) -> None:
        if not self._filter_active:
            return
        if event.is_printable and event.character:
            self._filter_query += event.character
            event.stop()
            self._update_subtitle()
            self._schedule_render()
        elif event.key in ("backspace", "ctrl+h"):
            if self._filter_query:
                self._filter_query = self._filter_query[:-1]
                event.stop()
                self._update_subtitle()
                self._schedule_render()

    # ── sort ─────────────────────────────────────────────────────────────────

    def action_cycle_sort(self) -> None:
        if self._filter_active:
            return
        idx = (_SORT_KEYS.index(self._sort_key) + 1) % len(_SORT_KEYS)
        self._sort_key = _SORT_KEYS[idx]
        self._sort_reverse = self._sort_key == "favorited_at"
        self._apply_sort()

    def action_toggle_reverse(self) -> None:
        if self._filter_active:
            return
        self._sort_reverse = not self._sort_reverse
        self._apply_sort()

    def _update_subtitle(self) -> None:
        container = self.query_one("#tracks-container", Vertical)
        if self._filter_active:
            container.border_subtitle = f"/ {self._filter_query}_"
        elif self._filter_query:
            container.border_subtitle = f"⌕ {self._filter_query}"
        else:
            arrow = "↓" if self._sort_reverse else "↑"
            label = dict(_SORT_OPTIONS)[self._sort_key]
            container.border_subtitle = f"{arrow} {label}"

    def _apply_sort(self) -> None:
        self._update_subtitle()
        self._render_list()

    # ── data ─────────────────────────────────────────────────────────────────

    def _sorted_tracks(self) -> list[Track]:
        def key(t: Track) -> object:
            if self._sort_key == "favorited_at":
                return t.favorited_at or 0
            if self._sort_key == "artist":
                return t.artist.lower()
            if self._sort_key == "title":
                return t.title.lower()
            if self._sort_key == "album":
                return t.album.lower()
            return 0

        return sorted(self._tracks, key=key, reverse=self._sort_reverse)

    def _filtered_tracks(self) -> list[Track]:
        tracks = self._sorted_tracks()
        if not self._filter_query:
            return tracks
        q = self._filter_query.lower()
        return [
            t
            for t in tracks
            if q in t.title.lower() or q in t.artist.lower() or q in t.album.lower()
        ]

    def _schedule_render(self) -> None:
        """Coalesce a burst of filter keystrokes into a single rebuild."""
        if self._filter_timer is not None:
            self._filter_timer.stop()
        self._filter_timer = self.set_timer(0.18, self._render_list)

    def _render_list(self) -> None:
        if self._filter_timer is not None:
            self._filter_timer.stop()
            self._filter_timer = None
        self._render_version += 1
        self._mount_rows(self._filtered_tracks(), self._render_version)

    @work
    async def _mount_rows(self, tracks: list[Track], version: int) -> None:
        if version != self._render_version:
            return
        lv = self.query_one("#fav-tracks", ListView)
        await lv.clear()
        if version != self._render_version:
            return
        if not tracks:
            msg = (
                "[dim]No matches.[/dim]"
                if self._filter_query
                else "[dim]No favourite tracks yet.[/dim]"
            )
            await lv.append(ListItem(Label(msg, markup=True)))
            return
        for i in range(0, len(tracks), _BATCH):
            if version != self._render_version:
                return
            await lv.mount(*[FavTrackRow(t) for t in tracks[i : i + _BATCH]])

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#fav-tracks", ListView)
        try:
            items = await app.ensure_favorite_tracks()
        except Exception as e:
            await lv.append(ListItem(Label(f"[red]{e}[/red]", markup=True)))
            return
        self._tracks = list(items)
        self._render_list()

    def favorite_changed(self, track_id: str, is_fav: bool) -> None:
        """Keep the favourites list in sync when a track is (un)favourited from
        anywhere — e.g. pressing `f` in the Queue or an album view."""
        if not self._loaded:
            return  # the first load will read the up-to-date app cache
        app: QobitApp = self.app  # type: ignore[assignment]
        if is_fav:
            if not any(t.id == track_id for t in self._tracks):
                self._tracks = list(app._fav_tracks or [])
                self._render_list()
        elif any(t.id == track_id for t in self._tracks):
            # Surgical removal preserves scroll/selection when unfavouriting here.
            self._tracks = [t for t in self._tracks if t.id != track_id]
            for row in self.query_one("#fav-tracks", ListView).query(FavTrackRow):
                if row.track.id == track_id:
                    row.remove()
                    break

    @on(ListView.Selected, "#fav-tracks")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, FavTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            lv = self.query_one("#fav-tracks", ListView)
            rows = list(lv.query(FavTrackRow))
            idx = rows.index(event.item)
            queue = [r.track for r in rows[idx + 1 :]]
            app.play_track(event.item.track, queue=queue)
            event.list_view.focus()
