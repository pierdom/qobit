"""qobit.qobuz.models — API-response parsing and derived display properties.

These are pure, dependency-free transforms (dict -> dataclass), so they're
cheap to pin down exactly. Several cases here encode quirks the Qobuz API
is known to vary on (performer vs. artist.name shapes, nested-track image
backfill) that CLAUDE.md documents as load-bearing — a regression here would
be a silent, hard-to-notice display bug, not a crash.
"""

from __future__ import annotations

from qobit.qobuz.models import Album, Artist, Playlist, StreamUrl, Track

# ── Track ──────────────────────────────────────────────────────────────────


def test_track_from_api_basic_fields():
    t = Track.from_api(
        {
            "id": 42,
            "title": "Hello",
            "duration": 185,
            "album": {"id": 7, "title": "Greetings", "image": {"large": "http://x/a.jpg"}},
            "performer": {"name": "Adele"},
        }
    )
    assert t.id == "42"  # ids are coerced to str throughout the app
    assert t.title == "Hello"
    assert t.artist == "Adele"
    assert t.album == "Greetings"
    assert t.album_id == "7"
    assert t.image_url == "http://x/a.jpg"
    assert t.duration_str == "3:05"


def test_track_artist_prefers_performer_name_string():
    """artist/get and favourite endpoints give performer.name as a plain string."""
    t = Track.from_api({"id": 1, "performer": {"name": "Radiohead"}, "artist": {"name": "Wrong"}})
    assert t.artist == "Radiohead"


def test_track_artist_falls_back_to_nested_display_dict():
    """artist/page top_tracks gives artist.name as {"display": "..."} instead
    of a plain string — Track.from_api must unwrap it, not stringify the dict."""
    t = Track.from_api({"id": 2, "artist": {"name": {"display": "Boards of Canada"}}})
    assert t.artist == "Boards of Canada"


def test_track_artist_falls_back_to_album_artist_when_no_performer_or_artist():
    t = Track.from_api({"id": 3, "album": {"artist": {"name": "Tame Impala"}}})
    assert t.artist == "Tame Impala"


def test_track_image_url_prefers_large_then_small():
    t_large = Track.from_api({"id": 1, "album": {"image": {"large": "L", "small": "S"}}})
    assert t_large.image_url == "L"
    t_small = Track.from_api({"id": 1, "album": {"image": {"small": "S"}}})
    assert t_small.image_url == "S"
    t_none = Track.from_api({"id": 1, "album": {"image": {}}})
    assert t_none.image_url is None


def test_track_display_title_appends_version_when_present():
    t = Track.from_api({"id": 1, "title": "Song", "version": "Live"})
    assert t.display_title == "Song (Live)"
    t_no_version = Track.from_api({"id": 1, "title": "Song"})
    assert t_no_version.display_title == "Song"


def test_track_duration_str_formats_minutes_seconds():
    t = Track.from_api({"id": 1, "duration": 65})
    assert t.duration_str == "1:05"
    t_zero = Track.from_api({"id": 1, "duration": 0})
    assert t_zero.duration_str == "0:00"


def test_track_favorited_at_defaults_to_none():
    assert Track.from_api({"id": 1}).favorited_at is None
    assert Track.from_api({"id": 1, "favorited_at": 100}).favorited_at == 100


# ── Album ──────────────────────────────────────────────────────────────────


def test_album_from_api_basic_fields():
    a = Album.from_api(
        {
            "id": 9,
            "title": "OK Computer",
            "artist": {"id": 55, "name": "Radiohead"},
            "release_date_original": "1997-05-21",
            "tracks_count": 12,
            "genre": {"name": "Alt Rock", "id": 3},
            "label": {"name": "Parlophone", "id": 4},
            "maximum_bit_depth": 24,
            "maximum_sampling_rate": 96.0,
            "hires_streamable": True,
        }
    )
    assert a.id == "9"
    assert a.artist_id == "55"
    assert a.year == 1997
    assert a.genre == "Alt Rock"
    assert a.genre_id == 3
    assert a.label == "Parlophone"
    assert a.label_id == 4
    assert a.quality_badge == "Hi-Res 24bit / 96kHz"


def test_album_quality_badge_empty_when_no_hires_metadata():
    a = Album.from_api({"id": 1, "title": "t", "artist": {}})
    assert a.quality_badge == ""


def test_album_quality_badge_omits_hires_prefix_when_not_hires_streamable():
    a = Album.from_api(
        {
            "id": 1,
            "title": "t",
            "artist": {},
            "maximum_bit_depth": 16,
            "maximum_sampling_rate": 44.1,
            "hires_streamable": False,
        }
    )
    assert a.quality_badge == "16bit / 44.1kHz"


def test_album_year_none_when_release_date_missing_or_malformed():
    assert Album.from_api({"id": 1, "title": "t", "artist": {}}).year is None
    assert (
        Album.from_api(
            {"id": 1, "title": "t", "artist": {}, "release_date_original": "not-a-date"}
        ).year
        is None
    )


def test_album_backfills_image_url_onto_tracks_missing_their_own():
    a = Album.from_api(
        {
            "id": 1,
            "title": "t",
            "artist": {},
            "image": {"large": "http://album/art.jpg"},
            "tracks": {"items": [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]},
        }
    )
    assert all(t.image_url == "http://album/art.jpg" for t in a.tracks)


def test_album_does_not_overwrite_a_track_image_that_already_has_one():
    a = Album.from_api(
        {
            "id": 1,
            "title": "t",
            "artist": {},
            "image": {"large": "http://album/art.jpg"},
            "tracks": {
                "items": [
                    {"id": 1, "title": "a", "album": {"image": {"large": "http://own.jpg"}}},
                ]
            },
        }
    )
    assert a.tracks[0].image_url == "http://own.jpg"


def test_album_awards_strips_whitespace_and_drops_unlabelled_entries():
    a = Album.from_api(
        {
            "id": 1,
            "title": "t",
            "artist": {},
            "awards": [{"label": "  Grammy  "}, {"name": "Mercury Prize"}, {}],
        }
    )
    assert a.awards == ["Grammy", "Mercury Prize"]


def test_album_total_duration_str_empty_with_no_tracks():
    assert Album.from_api({"id": 1, "title": "t", "artist": {}}).total_duration_str == ""


def test_album_total_duration_str_sums_track_durations():
    a = Album.from_api(
        {
            "id": 1,
            "title": "t",
            "artist": {},
            "tracks": {"items": [{"id": 1, "duration": 120}, {"id": 2, "duration": 90}]},
        }
    )
    assert a.total_duration_str == "3:30"


# ── Artist ─────────────────────────────────────────────────────────────────


def test_artist_from_api_basic_fields():
    a = Artist.from_api(
        {
            "id": 1,
            "name": "Björk",
            "image": {"mega": "M", "large": "L"},
            "biography": {"content": "A bio"},
        }
    )
    assert a.name == "Björk"
    assert a.image_url == "M"  # mega preferred over large
    assert a.biography == "A bio"


def test_artist_biography_falls_back_to_summary():
    a = Artist.from_api({"id": 1, "name": "x", "biography": {"summary": "Short bio"}})
    assert a.biography == "Short bio"


def test_artist_albums_sorted_newest_first():
    a = Artist.from_api(
        {
            "id": 1,
            "name": "x",
            "albums": {
                "items": [
                    {"id": 1, "title": "Old", "artist": {}, "release_date_original": "1990-01-01"},
                    {"id": 2, "title": "New", "artist": {}, "release_date_original": "2020-01-01"},
                ]
            },
        }
    )
    assert [al.title for al in a.albums] == ["New", "Old"]


# ── Playlist ───────────────────────────────────────────────────────────────


def test_playlist_from_api_basic_fields():
    p = Playlist.from_api(
        {
            "id": 1,
            "name": "Road trip",
            "owner": {"name": "pierdom"},
            "tracks_count": 20,
            "images": ["http://img.jpg"],
            "duration": 3725,
        }
    )
    assert p.owner == "pierdom"
    assert p.image_url == "http://img.jpg"
    assert p.duration_str == "1:02:05"


def test_playlist_image_url_none_when_no_images():
    p = Playlist.from_api({"id": 1, "name": "x", "owner": {}, "tracks_count": 0})
    assert p.image_url is None


def test_playlist_duration_str_empty_when_zero():
    p = Playlist.from_api({"id": 1, "name": "x", "owner": {}, "tracks_count": 0})
    assert p.duration_str == ""


def test_playlist_date_str_prefers_updated_over_created():
    p = Playlist.from_api(
        {
            "id": 1,
            "name": "x",
            "owner": {},
            "tracks_count": 0,
            "created_at": 1577836800,  # 2020-01-01
            "updated_at": 1609459200,  # 2021-01-01
        }
    )
    assert p.date_str == "2021-01-01"


# ── StreamUrl ──────────────────────────────────────────────────────────────


def test_stream_url_from_api_converts_khz_to_hz():
    s = StreamUrl.from_api({"url": "u", "sampling_rate": 192})
    assert s.sampling_rate == 192000.0


def test_stream_url_quality_badge():
    s = StreamUrl.from_api({"url": "u", "sampling_rate": 192, "bit_depth": 24})
    assert s.quality_badge == "[ 24-BIT · 192 kHz ]"


def test_stream_url_quality_formats_non_integer_khz_with_one_decimal():
    s = StreamUrl.from_api({"url": "u", "sampling_rate": 44.1, "bit_depth": 16})
    assert s.quality_badge == "[ 16-BIT · 44.1 kHz ]"


def test_stream_url_defaults_bit_depth_to_16_when_missing():
    s = StreamUrl.from_api({"url": "u", "sampling_rate": 44.1})
    assert s.quality_badge == "[ 16-BIT · 44.1 kHz ]"


def test_stream_url_sample_flag_defaults_false():
    assert StreamUrl.from_api({"url": "u", "sampling_rate": 44.1}).sample is False


def test_stream_url_detects_preview_sample():
    # Shape of a real getFileUrl answer for a track the account can't stream.
    s = StreamUrl.from_api(
        {
            "url": "u",
            "format_id": 5,
            "mime_type": "audio/mpeg",
            "sample": True,
            "duration": 30,
            "sampling_rate": 44.1,
            "bit_depth": 16,
            "restrictions": [{"code": "TrackRestrictedByPurchaseCredentials"}],
        }
    )
    assert s.sample is True


def test_playlist_prefers_300px_covers_over_50px_thumbnails():
    p = Playlist.from_api(
        {
            "id": 1,
            "name": "x",
            "owner": {},
            "tracks_count": 0,
            "images": ["http://c/a_50.jpg"],
            "images150": ["http://c/a_150.jpg"],
            "images300": ["http://c/a_300.jpg"],
        }
    )
    assert p.image_url == "http://c/a_300.jpg"


def test_playlist_falls_back_to_150_then_50px():
    base = {"id": 1, "name": "x", "owner": {}, "tracks_count": 0}
    p150 = Playlist.from_api({**base, "images": ["a_50"], "images150": ["a_150"]})
    assert p150.image_url == "a_150"
    assert Playlist.from_api({**base, "images": ["a_50"]}).image_url == "a_50"


def test_album_backfills_its_identity_onto_nested_tracks():
    """album/get's nested tracks carry no album: without the backfill a track
    played from an album page had no album id, and "Go to album" failed."""
    album = Album.from_api(
        {
            "id": "abc123",
            "title": "The Axeman Cometh",
            "image": {"large": "https://img/cover.jpg"},
            "tracks": {"items": [{"id": 1, "title": "Out of the Dark"}]},
        }
    )
    t = album.tracks[0]
    assert (t.album_id, t.album, t.image_url) == (
        "abc123",
        "The Axeman Cometh",
        "https://img/cover.jpg",
    )


def test_artist_without_photo_uses_newest_release_cover():
    """Qobuz has no photo for some artists. Fall back to the newest release's
    cover, picked by date: the API's album order isn't chronological."""
    artist = Artist.from_api(
        {
            "id": 1,
            "name": "Besso0",
            "image": None,
            "albums": {
                "items": [
                    {"id": "a", "title": "Old", "released_at": 100, "image": {"large": "old.jpg"}},
                    {"id": "b", "title": "New", "released_at": 300, "image": {"large": "new.jpg"}},
                    {"id": "c", "title": "Mid", "released_at": 200, "image": {"large": "mid.jpg"}},
                    {"id": "d", "title": "No art", "released_at": 400, "image": None},
                ]
            },
        }
    )
    assert artist.image_url == "new.jpg"


def test_artist_photo_wins_over_album_cover():
    artist = Artist.from_api(
        {
            "id": 1,
            "name": "Mogwai",
            "image": {"large": "photo.jpg"},
            "albums": {"items": [{"id": "a", "released_at": 1, "image": {"large": "c.jpg"}}]},
        }
    )
    assert artist.image_url == "photo.jpg"


def test_track_matches_performer_or_album_artist():
    """A soundtrack track performed by Aimer on a Kajiura Yuki album counts
    as a favourite on both artists' pages."""
    t = Track.from_api(
        {
            "id": 1,
            "title": "Zankyou Sanka",
            "performer": {"id": 11, "name": "Aimer"},
            "album": {"id": "a", "title": "OST", "artist": {"id": 22, "name": "Kajiura Yuki"}},
        }
    )
    assert (t.artist_id, t.album_artist_id) == ("11", "22")
    assert t.by_artist("11") and t.by_artist("22") and not t.by_artist("33")


def test_saved_tracks_without_artist_ids_still_load():
    """queue_state.json written before artist ids existed must still restore."""
    t = Track(id="1", title="T", artist="A", album="B", album_id="b", duration=1)
    assert t.artist_id is None and not t.by_artist("1")
