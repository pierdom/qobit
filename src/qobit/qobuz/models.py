from dataclasses import dataclass, field


def _khz_str(khz: float) -> str:
    """Format a kHz value, dropping the decimal when it's a whole number
    (``192`` -> ``"192"``, ``44.1`` -> ``"44.1"``). Callers must pass a value
    already scaled to kHz — StreamUrl.sampling_rate is stored in Hz while
    Album.maximum_sampling_rate is already kHz, so the conversion stays at
    the call site rather than being guessed here."""
    return f"{int(khz)}" if khz == int(khz) else f"{khz:.1f}"


def quality_text(bit_depth: int, khz: float) -> str:
    """The one way qobit writes a resolution: ``24-BIT · 96 kHz``."""
    return f"{bit_depth}-BIT · {_khz_str(khz)} kHz"


class NoStreamError(Exception):
    """A stream request answered without a URL (restriction codes only)."""


@dataclass
class StreamUrl:
    url: str
    format_id: int
    sampling_rate: float  # Hz
    bit_depth: int | None
    # True when Qobuz answered with a ~30 s MP3 preview instead of the real file
    # (e.g. restriction TrackRestrictedByPurchaseCredentials). It's returned at
    # *every* requested quality, so a quality fallback never escapes it — and
    # playing it would silently break the bit-perfect FLAC guarantee.
    sample: bool = False

    @classmethod
    def from_api(cls, data: dict) -> "StreamUrl":
        """Raises NoStreamError when Qobuz sends no URL: it answers some
        tracks with only restriction codes (SampleRestrictedByRightHolders),
        and a KeyError there closed the app."""
        url = data.get("url")
        if not url:
            codes = [r["code"] for r in data.get("restrictions") or [] if r and r.get("code")]
            raise NoStreamError(", ".join(codes) or "no stream URL")
        return cls(
            url=url,
            format_id=data.get("format_id", 6),
            sampling_rate=float(data.get("sampling_rate", 44.1)) * 1000,
            bit_depth=data.get("bit_depth"),
            sample=bool(data.get("sample", False)),
        )

    @property
    def quality_badge(self) -> str:
        """Console-readout style for the transport bar, e.g. [ 24-BIT · 192 kHz ]."""
        bd = self.bit_depth or 16
        return f"[ {quality_text(bd, self.sampling_rate / 1000)} ]"


def release_year(album: dict) -> int | None:
    """An album's original release year, from ``release_date_original``
    ("1959-08-17"); None when the API doesn't give one."""
    release = album.get("release_date_original") or ""
    return int(release[:4]) if len(release) >= 4 and release[:4].isdigit() else None


@dataclass
class Track:
    id: str
    title: str
    artist: str
    album: str
    album_id: str
    duration: int  # seconds
    version: str | None = None
    image_url: str | None = None
    favorited_at: int | None = None
    # Who performs it, and whose album it's on: they differ on compilations and
    # soundtracks. The artist page's "Your favourites" matches either.
    artist_id: str | None = None
    album_artist_id: str | None = None
    # The album's original release year (the transport shows "Album (1959)").
    # None in queues saved before it existed: they load unchanged.
    album_year: int | None = None

    @classmethod
    def from_api(cls, data: dict) -> "Track":
        album = data.get("album") or {}
        image = album.get("image") or {}

        def _name(obj: dict) -> str:
            # artist/get: name is a str; artist/page top_tracks: name is {"display": "..."}
            n = obj.get("name", "")
            return n.get("display", "") if isinstance(n, dict) else n

        performer = data.get("performer") or data.get("artist") or {}
        album_artist = album.get("artist") or {}
        artist = (
            (data.get("performer") or {}).get("name", "")
            or _name(data.get("artist") or {})
            or _name(album_artist)
        )
        return cls(
            id=str(data["id"]),
            title=data.get("title", ""),
            artist=artist,
            album=album.get("title", ""),
            album_id=str(album.get("id", "")),
            duration=data.get("duration", 0),
            version=data.get("version") or None,
            image_url=image.get("large") or image.get("small") or None,
            favorited_at=data.get("favorited_at") or None,
            artist_id=str(performer["id"]) if performer.get("id") else None,
            album_artist_id=str(album_artist["id"]) if album_artist.get("id") else None,
            album_year=release_year(album),
        )

    def by_artist(self, artist_id: str) -> bool:
        return artist_id in (self.artist_id, self.album_artist_id)

    @property
    def display_title(self) -> str:
        return f"{self.title} ({self.version})" if self.version else self.title

    @property
    def duration_str(self) -> str:
        m, s = divmod(self.duration, 60)
        return f"{m}:{s:02d}"


@dataclass
class Album:
    id: str
    title: str
    artist: str
    year: int | None
    tracks_count: int
    artist_id: str | None = None
    genre: str | None = None
    genre_id: int | None = None
    description: str | None = None
    label: str | None = None
    label_id: int | None = None
    version: str | None = None
    maximum_bit_depth: int | None = None
    maximum_sampling_rate: float | None = None
    hires_streamable: bool = False
    popularity: int | None = None
    awards: list[str] = field(default_factory=list)
    image_url: str | None = None
    tracks: list[Track] = field(default_factory=list)
    favorited_at: int | None = None

    @property
    def total_duration_str(self) -> str:
        secs = sum(t.duration for t in self.tracks)
        if not secs:
            return ""
        h, r = divmod(secs, 3600)
        m, s = divmod(r, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @property
    def quality_badge(self) -> str:
        if not self.maximum_bit_depth or not self.maximum_sampling_rate:
            return ""
        badge = quality_text(self.maximum_bit_depth, self.maximum_sampling_rate)
        return f"HI-RES {badge}" if self.hires_streamable else badge

    @classmethod
    def from_api(cls, data: dict) -> "Album":
        tracks = [Track.from_api(t) for t in (data.get("tracks") or {}).get("items") or []]
        image = data.get("image") or {}
        img_url = image.get("large") or image.get("small") or None
        # Track items nested under an album carry no album of their own, so
        # Track.from_api leaves image_url, album and album_id unset. Backfill
        # them from the album: the now-playing art (transport bar, OS media
        # controls) and "Go to album" need them when a track is played
        # straight from the album view or its play queue.
        album_id = str(data["id"])
        year = release_year(data)
        for t in tracks:
            t.image_url = t.image_url or img_url
            t.album = t.album or data.get("title", "")
            t.album_id = t.album_id or album_id
            t.album_year = t.album_year or year
        awards = [
            name
            for a in (data.get("awards") or [])
            if (name := (a.get("label") or a.get("name") or "").strip())
        ]
        artist_data = data.get("artist") or {}
        return cls(
            id=album_id,
            title=data.get("title", ""),
            artist=artist_data.get("name", ""),
            artist_id=str(artist_data["id"]) if artist_data.get("id") else None,
            year=year,
            tracks_count=data.get("tracks_count", len(tracks)),
            genre=(data.get("genre") or {}).get("name") or None,
            genre_id=(data.get("genre") or {}).get("id"),
            description=data.get("description") or None,
            label=(data.get("label") or {}).get("name") or None,
            label_id=(data.get("label") or {}).get("id"),
            version=data.get("version") or None,
            maximum_bit_depth=data.get("maximum_bit_depth") or None,
            maximum_sampling_rate=data.get("maximum_sampling_rate") or None,
            hires_streamable=bool(data.get("hires_streamable", False)),
            popularity=data.get("popularity") or None,
            awards=awards,
            image_url=img_url,
            tracks=tracks,
            favorited_at=data.get("favorited_at") or None,
        )


def _newest_cover(albums: list[dict]) -> str | None:
    """Cover of the most recent release that has one. The API's album order
    isn't by date (its sort parameter is ignored), so pick by `released_at`."""
    with_cover = [a for a in albums if (a.get("image") or {}).get("large")]
    if not with_cover:
        return None
    return max(with_cover, key=lambda a: a.get("released_at") or 0)["image"]["large"]


@dataclass
class Artist:
    id: str
    name: str
    albums_count: int | None = None
    image_url: str | None = None
    biography: str | None = None
    tracks: list[Track] = field(default_factory=list)
    albums: list["Album"] = field(default_factory=list)
    favorited_at: int | None = None

    @classmethod
    def from_api(cls, data: dict) -> "Artist":
        image = data.get("image") or {}
        bio = data.get("biography") or {}
        tracks = [Track.from_api(t) for t in (data.get("tracks") or {}).get("items") or []]
        raw_albums = (data.get("albums") or {}).get("items") or []
        albums = sorted(
            [Album.from_api(a) for a in raw_albums],
            key=lambda a: a.year or 0,
            reverse=True,
        )
        return cls(
            id=str(data["id"]),
            name=data.get("name", ""),
            albums_count=data.get("albums_count"),
            # Qobuz has no photo for some artists (about 1 in 10 here): show
            # their newest release's cover instead of an empty tile.
            image_url=image.get("mega") or image.get("large") or _newest_cover(raw_albums),
            biography=bio.get("content") or bio.get("summary") or None,
            tracks=tracks,
            albums=albums,
            favorited_at=data.get("favorited_at") or None,
        )


@dataclass
class Playlist:
    id: str
    name: str
    owner: str
    tracks_count: int
    image_url: str | None = None
    tracks: list[Track] = field(default_factory=list)
    duration: int = 0
    created_at: int | None = None
    updated_at: int | None = None
    description: str | None = None

    @property
    def duration_str(self) -> str:
        if not self.duration:
            return ""
        h, r = divmod(self.duration, 3600)
        m, s = divmod(r, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @property
    def date_str(self) -> str:
        from datetime import datetime

        ts = self.updated_at or self.created_at
        if not ts:
            return ""
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

    @classmethod
    def from_api(cls, data: dict) -> "Playlist":
        tracks = [Track.from_api(t) for t in (data.get("tracks") or {}).get("items") or []]
        # Prefer the 300 px covers: "images" holds 50 px thumbnails, which
        # turned visibly blurry once scaled up to a grid tile.
        images = data.get("images300") or data.get("images150") or data.get("images") or []
        return cls(
            id=str(data["id"]),
            name=data.get("name", ""),
            owner=(data.get("owner") or {}).get("name", ""),
            tracks_count=data.get("tracks_count", len(tracks)),
            image_url=images[0] if images else None,
            tracks=tracks,
            duration=data.get("duration", 0),
            created_at=data.get("created_at") or None,
            updated_at=data.get("updated_at") or None,
            description=data.get("description") or None,
        )
