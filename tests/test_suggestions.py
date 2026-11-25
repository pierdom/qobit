"""SuggestionService: song radio and the mixes, against a fake Qobuz client."""

from __future__ import annotations

import asyncio

from qobit.qobuz.client import QobuzError
from qobit.qobuz.models import Album, Track
from qobit.qobuz.suggestions import MIX_ANALYSED, MIX_SEEDS, SuggestionService, as_int


def _track(n: int) -> Track:
    return Track(id=str(n), title=f"T{n}", artist="A", album="B", album_id="1", duration=100)


def _item(n: int) -> dict:
    return {
        "id": n,
        "title": f"S{n}",
        "performer": {"name": "A"},
        "album": {"title": "B"},
        "duration": 100,
    }


class Client:
    """Records dynamic/suggest calls; answers track/get from a table."""

    def __init__(self, suggestions=(), details=None, suggest_error=False):
        self.suggestions = list(suggestions)
        self.details = details if details is not None else {}
        self.suggest_error = suggest_error
        self.calls: list[tuple[list[int], list[dict]]] = []
        self.track_gets: list[str] = []

    async def get_dynamic_suggestions(self, listened, analysed, limit=50):
        self.calls.append((list(listened), list(analysed)))
        if self.suggest_error:
            raise QobuzError("down")
        return {"tracks": {"items": self.suggestions}}

    async def get_track(self, track_id):
        self.track_gets.append(track_id)
        answer = self.details.get(track_id)
        if isinstance(answer, Exception):
            raise answer
        return answer or {}


def _service(**kw) -> tuple[SuggestionService, Client]:
    client = Client(**kw)
    return SuggestionService(client), client  # type: ignore[arg-type]


def _detail(artist=1, label=2, genre=3) -> dict:
    return {
        "album": {"artist": {"id": artist}, "label": {"id": label}, "genre": {"id": genre}},
    }


def _album(**ids) -> Album:
    album = Album(id="1", title="B", artist="A", year=2020, tracks_count=1)
    album.artist_id, album.label_id, album.genre_id = (
        ids.get("a", 7),
        ids.get("l", 8),
        ids.get("g", 9),
    )
    return album


def test_as_int_takes_numbers_and_numeric_strings_only():
    assert (as_int(5), as_int("42"), as_int(None), as_int("x")) == (5, 42, None, None)


def test_radio_sends_the_seed_first_then_recent_history_up_to_ten():
    svc, client = _service(details={"100": _detail()})
    history = [_track(i) for i in range(1, 30)]  # oldest first
    asyncio.run(svc.radio(_track(100), history))
    listened, _ = client.calls[0]
    assert listened == [100, 29, 28, 27, 26, 25, 24, 23, 22, 21]


def test_radio_does_not_repeat_a_listened_id():
    svc, client = _service(details={"5": _detail()})
    asyncio.run(svc.radio(_track(5), [_track(5), _track(6), _track(6)]))
    assert client.calls[0][0] == [5, 6]


def test_radio_uses_the_known_album_instead_of_asking_for_the_track():
    svc, client = _service()
    asyncio.run(svc.radio(_track(5), [], seed_album=_album()))
    assert client.track_gets == []
    assert client.calls[0][1] == [{"track_id": 5, "artist_id": 7, "label_id": 8, "genre_id": 9}]


def test_radio_asks_for_the_track_when_the_album_is_not_known():
    svc, client = _service(details={"5": _detail(artist="11", label=None, genre="13")})
    asyncio.run(svc.radio(_track(5), []))
    assert client.track_gets == ["5"]
    assert client.calls[0][1] == [
        {"track_id": 5, "artist_id": 11, "label_id": None, "genre_id": 13}
    ]


def test_radio_still_asks_when_the_track_details_fail_or_have_no_ids():
    svc, client = _service(details={"5": QobuzError("x"), "6": {"album": {"label": {"id": 1}}}})
    asyncio.run(svc.radio(_track(5), []))
    asyncio.run(svc.radio(_track(6), []))
    assert [analysed for _, analysed in client.calls] == [[], []]  # label alone isn't enough


def test_radio_returns_new_tracks_only_once_each_and_skips_junk():
    items = [_item(1), "junk", _item(2), _item(2), _item(5), _item(3)]
    svc, _ = _service(suggestions=items, details={"5": _detail()})
    got = asyncio.run(svc.radio(_track(5), [_track(1)]))
    assert [t.id for t in got] == ["2", "3"]  # 1 and 5 were listened; 2 once; junk skipped


def test_radio_is_empty_when_the_request_fails():
    svc, _ = _service(suggest_error=True, details={"5": _detail()})
    assert asyncio.run(svc.radio(_track(5), [])) == []


def test_a_mix_with_no_seeds_asks_nobody():
    svc, client = _service()
    assert asyncio.run(svc.mix([], [])) == []
    assert client.calls == []


def test_a_mix_seeds_history_most_recent_first_then_favourites_without_repeats():
    svc, client = _service()
    history = [_track(1), _track(2), _track(3)]
    favourites = [_track(3), _track(4)]
    asyncio.run(svc.mix(history, favourites))
    assert client.calls[0][0] == [3, 2, 1, 4]


def test_a_mix_is_capped_and_analyses_a_spread_including_both_ends():
    svc, client = _service()
    favourites = [_track(i) for i in range(1, 80)]
    asyncio.run(svc.mix([], favourites))
    listened, _ = client.calls[0]
    assert len(listened) == MIX_SEEDS
    assert sorted(set(client.track_gets), key=int)[0] == "1"
    assert "50" in client.track_gets  # the last seed is in the spread
    assert len(client.track_gets) == MIX_ANALYSED


def test_a_mix_drops_an_analysis_that_fails_and_still_asks():
    details = {"1": _detail(), "2": QobuzError("x"), "3": _detail(artist=9)}
    svc, client = _service(details=details)
    asyncio.run(svc.mix([], [_track(1), _track(2), _track(3)]))
    analysed = client.calls[0][1]
    assert {e["track_id"] for e in analysed} == {1, 3}


def test_a_mix_returns_new_tracks_and_is_empty_when_the_request_fails():
    svc, _ = _service(suggestions=[_item(1), _item(9), _item(9), "junk"], details={"1": _detail()})
    assert [t.id for t in asyncio.run(svc.mix([], [_track(1)]))] == ["9"]
    svc, _ = _service(suggest_error=True, details={"1": _detail()})
    assert asyncio.run(svc.mix([], [_track(1)])) == []
