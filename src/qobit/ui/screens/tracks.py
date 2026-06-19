from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from ...qobuz.models import Track
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


class FavTrackRow(ListItem):
    DEFAULT_CSS = """
    FavTrackRow { height: 2; padding: 0 1; }
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
    DEFAULT_CSS = """
    TracksView {
        height: 1fr;
        layout: vertical;
    }
    TracksView ListView {
        height: 1fr;
        border: round $accent 40%;
        border-title-color: $accent 40%;
        border-title-style: bold;
        border-subtitle-color: $accent 40%;
        border-subtitle-align: right;
        margin: 0 1 1 1;
    }
    TracksView ListView:focus {
        border: round $accent;
        border-title-color: $accent;
        border-subtitle-color: $accent;
    }
    """

    _loaded: bool = False
    _sort_key: str = "favorited_at"
    _sort_reverse: bool = True
    _tracks: list[Track]

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._tracks = []

    def compose(self) -> ComposeResult:
        yield ListView(id="fav-tracks")

    def on_mount(self) -> None:
        lv = self.query_one("#fav-tracks", ListView)
        lv.border_title = "Favourite Tracks"
        self._update_subtitle()

    def on_show(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._load()
        self.call_after_refresh(self.query_one("#fav-tracks", ListView).focus)

    def action_cycle_sort(self) -> None:
        idx = (_SORT_KEYS.index(self._sort_key) + 1) % len(_SORT_KEYS)
        self._sort_key = _SORT_KEYS[idx]
        self._sort_reverse = self._sort_key == "favorited_at"
        self._apply_sort()

    def action_toggle_reverse(self) -> None:
        self._sort_reverse = not self._sort_reverse
        self._apply_sort()

    def _update_subtitle(self) -> None:
        arrow = "↓" if self._sort_reverse else "↑"
        label = dict(_SORT_OPTIONS)[self._sort_key]
        self.query_one("#fav-tracks", ListView).border_subtitle = f"{arrow} {label}"

    def _apply_sort(self) -> None:
        self._update_subtitle()
        self._render_list()

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

    def _render_list(self) -> None:
        self._mount_rows(self._sorted_tracks())

    @work
    async def _mount_rows(self, tracks: list[Track]) -> None:
        lv = self.query_one("#fav-tracks", ListView)
        await lv.clear()
        if not tracks:
            await lv.append(ListItem(Label("[dim]No favourite tracks yet.[/dim]", markup=True)))
            return
        await lv.mount(*[FavTrackRow(t) for t in tracks])

    @work
    async def _load(self) -> None:
        app: QobitApp = self.app  # type: ignore[assignment]
        lv = self.query_one("#fav-tracks", ListView)
        try:
            items = await app._client.get_all_favorite_tracks()
        except Exception as e:
            await lv.append(ListItem(Label(f"[red]{e}[/red]", markup=True)))
            return
        self._tracks = [Track.from_api(raw) for raw in items]
        self._render_list()

    @on(ListView.Selected, "#fav-tracks")
    def _on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, FavTrackRow):
            app: QobitApp = self.app  # type: ignore[assignment]
            app.play_track(event.item.track)
