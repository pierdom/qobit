from dataclasses import dataclass, field


@dataclass
class StreamUrl:
    url: str
    format_id: int
    mime_type: str
    sampling_rate: float
    bit_depth: int | None
    file_size: int | None = None

    @classmethod
    def from_api(cls, data: dict) -> "StreamUrl":
        return cls(
            url=data["url"],
            format_id=data.get("format_id", 6),
            mime_type=data.get("mime_type", "audio/flac"),
            sampling_rate=float(data.get("sampling_rate", 44.1)) * 1000,
            bit_depth=data.get("bit_depth"),
            file_size=data.get("file_size"),
        )

    @property
    def quality_label(self) -> str:
        bd = self.bit_depth or 16
        sr_khz = self.sampling_rate / 1000
        sr_str = f"{int(sr_khz)}" if sr_khz == int(sr_khz) else f"{sr_khz:.1f}"
        return f"{bd}/{sr_str}"


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

    @classmethod
    def from_api(cls, data: dict) -> "Track":
        album = data.get("album", {})
        image = album.get("image", {})

        def _name(obj: dict) -> str:
            # artist/get: name is a str; artist/page top_tracks: name is {"display": "..."}
            n = obj.get("name", "")
            return n.get("display", "") if isinstance(n, dict) else n

        artist = (
            data.get("performer", {}).get("name", "")
            or _name(data.get("artist", {}))
            or _name(album.get("artist", {}))
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
        )

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
    description: str | None = None
    label: str | None = None
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
        sr = self.maximum_sampling_rate
        sr_str = f"{int(sr)}" if sr == int(sr) else f"{sr:.1f}"
        badge = f"{self.maximum_bit_depth}bit / {sr_str}kHz"
        return f"Hi-Res {badge}" if self.hires_streamable else badge

    @classmethod
    def from_api(cls, data: dict) -> "Album":
        tracks = [Track.from_api(t) for t in data.get("tracks", {}).get("items", [])]
        image = data.get("image", {})
        release = data.get("release_date_original", "") or ""
        year = int(release[:4]) if len(release) >= 4 and release[:4].isdigit() else None
        awards = [
            name
            for a in (data.get("awards") or [])
            if (name := (a.get("label") or a.get("name") or "").strip())
        ]
        artist_data = data.get("artist", {})
        return cls(
            id=str(data["id"]),
            title=data.get("title", ""),
            artist=artist_data.get("name", ""),
            artist_id=str(artist_data["id"]) if artist_data.get("id") else None,
            year=year,
            tracks_count=data.get("tracks_count", len(tracks)),
            genre=data.get("genre", {}).get("name") or None,
            description=data.get("description") or None,
            label=(data.get("label") or {}).get("name") or None,
            version=data.get("version") or None,
            maximum_bit_depth=data.get("maximum_bit_depth") or None,
            maximum_sampling_rate=data.get("maximum_sampling_rate") or None,
            hires_streamable=bool(data.get("hires_streamable", False)),
            popularity=data.get("popularity") or None,
            awards=awards,
            image_url=image.get("large") or image.get("small") or None,
            tracks=tracks,
            favorited_at=data.get("favorited_at") or None,
        )


@dataclass
class Artist:
    id: str
    name: str
    albums_count: int | None = None
    image_url: str | None = None
    biography: str | None = None
    tracks: list[Track] = field(default_factory=list)
    albums: list["Album"] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> "Artist":
        image = data.get("image") or {}
        bio = data.get("biography") or {}
        tracks = [Track.from_api(t) for t in data.get("tracks", {}).get("items", [])]
        albums = sorted(
            [Album.from_api(a) for a in data.get("albums", {}).get("items", [])],
            key=lambda a: a.year or 0,
            reverse=True,
        )
        return cls(
            id=str(data["id"]),
            name=data.get("name", ""),
            albums_count=data.get("albums_count"),
            image_url=image.get("mega") or image.get("large") or None,
            biography=bio.get("content") or bio.get("summary") or None,
            tracks=tracks,
            albums=albums,
        )


@dataclass
class Playlist:
    id: str
    name: str
    owner: str
    tracks_count: int
    image_url: str | None = None
    tracks: list[Track] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> "Playlist":
        tracks = [Track.from_api(t) for t in data.get("tracks", {}).get("items", [])]
        images = data.get("images") or []
        return cls(
            id=str(data["id"]),
            name=data.get("name", ""),
            owner=(data.get("owner") or {}).get("name", ""),
            tracks_count=data.get("tracks_count", len(tracks)),
            image_url=images[0] if images else None,
            tracks=tracks,
        )
