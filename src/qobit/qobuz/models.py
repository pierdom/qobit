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
            sampling_rate=float(data.get("sampling_rate", 44100)),
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
        return cls(
            id=str(data["id"]),
            title=data.get("title", ""),
            artist=(
                data.get("performer", {}).get("name", "") or album.get("artist", {}).get("name", "")
            ),
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
    genre: str | None = None
    image_url: str | None = None
    tracks: list[Track] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> "Album":
        tracks = [Track.from_api(t) for t in data.get("tracks", {}).get("items", [])]
        image = data.get("image", {})
        release = data.get("release_date_original", "") or ""
        year = int(release[:4]) if len(release) >= 4 and release[:4].isdigit() else None
        return cls(
            id=str(data["id"]),
            title=data.get("title", ""),
            artist=data.get("artist", {}).get("name", ""),
            year=year,
            tracks_count=data.get("tracks_count", len(tracks)),
            genre=data.get("genre", {}).get("name") or None,
            image_url=image.get("large") or image.get("small") or None,
            tracks=tracks,
        )


@dataclass
class Artist:
    id: str
    name: str
    albums_count: int | None = None
    image_url: str | None = None

    @classmethod
    def from_api(cls, data: dict) -> "Artist":
        image = data.get("image") or {}
        return cls(
            id=str(data["id"]),
            name=data.get("name", ""),
            albums_count=data.get("albums_count"),
            image_url=image.get("large") or None,
        )
