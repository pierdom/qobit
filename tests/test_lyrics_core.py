"""Lyrics, UI-agnostic: the LRC reader, judging whether lyrics belong to a
track, the disk cache, the LRCLIB provider (a mock transport: nothing goes out)
and the service that ties them."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time

import httpx
import pytest

from qobit.lyrics import LyricsService, Query
from qobit.lyrics import cache as lyrics_cache
from qobit.lyrics.lrc import parse_lrc, parse_plain
from qobit.lyrics.matching import Candidate, fold, pick
from qobit.lyrics.model import Line, Lyrics
from qobit.lyrics.providers import LrcLib, ProviderError

# ── the LRC reader ───────────────────────────────────────────────────────────


def test_lrc_lines_and_stamp_shapes():
    ly = parse_lrc(
        "[ar:Someone]\n[00:12.5]half\n[01:02.34]hundredths\n[01:03.123]thousandths\n[02:00]whole"
    )
    assert ly.synced
    assert [(round(x.time, 3), x.text) for x in ly.lines] == [
        (12.5, "half"),  # ".5" is half a second, not 5 ms
        (62.34, "hundredths"),
        (63.123, "thousandths"),
        (120.0, "whole"),
    ]


def test_lrc_a_refrain_with_several_stamps_is_several_lines_in_time_order():
    ly = parse_lrc("[00:10.00][00:50.00]chorus\n[00:30.00]verse")
    assert [(x.time, x.text) for x in ly.lines] == [
        (10.0, "chorus"),
        (30.0, "verse"),
        (50.0, "chorus"),
    ]


def test_lrc_enhanced_word_stamps_are_timing_not_text():
    ly = parse_lrc("[00:05.00]<00:05.00>Hello <00:05.50>world")
    assert ly.lines == [Line("Hello world", 5.0)]


def test_lrc_an_empty_stamp_is_kept_it_clears_the_line():
    ly = parse_lrc("[00:05.00]sung\n[00:09.00]\n[00:20.00]again")
    assert [x.text for x in ly.lines] == ["sung", "", "again"]


def test_lrc_offset_tag_shifts_the_lyrics_earlier_when_positive():
    ly = parse_lrc("[offset:500]\n[00:10.00]x\n[00:00.20]early")
    assert [x.time for x in ly.lines] == [0.0, 9.5]  # never below zero


def test_lrc_with_no_stamps_is_not_synced():
    assert parse_lrc("just words\nno times").synced is False


def test_plain_text_keeps_stanzas_once_and_trims_the_end():
    ly = parse_plain("one\ntwo\n\n\n\nthree\n\n")
    assert [x.text for x in ly.lines] == ["one", "two", "", "three"]
    assert not ly.synced and all(x.time is None for x in ly.lines)


def test_line_at_follows_the_position():
    ly = parse_lrc("[00:10.00]a\n[00:20.00]b")
    assert [ly.line_at(t) for t in (0, 9.9, 10, 19.9, 20, 300)] == [-1, -1, 0, 0, 1, 1]


# ── matching ─────────────────────────────────────────────────────────────────


def test_fold_ignores_case_accents_punctuation_features_and_brackets():
    assert fold("Beyoncé") == fold("BEYONCE")
    assert fold("Creep (Remastered 2011)") == fold("creep")
    assert fold("Get Lucky (feat. Pharrell Williams)") == fold("Get Lucky")
    assert fold("Time - 2011 Remastered Version") == fold("Time")
    assert fold("Rock & Roll") == fold("Rock and Roll")
    assert fold("(Live)") == "" and fold("Hurt - Live at Folsom") == "hurt"  # the title stays
    assert (
        fold("Symphony No. 5 - I. Allegro") == "symphony no 5 i allegro"
    )  # a subtitle isn't a version


def _cand(**kw) -> Candidate:
    base = {"title": "Creep", "artist": "Radiohead", "album": "Pablo Honey", "duration": 238.0}
    return Candidate(**{**base, **kw})


Q = Query("Creep", "Radiohead", "Pablo Honey", 238)
SYNC = "[00:10.00]When you were here before"


def test_pick_takes_timed_lyrics_of_the_same_song():
    got = pick(Q, [_cand(synced=SYNC, plain="When you were here before")])
    assert got is not None and got.synced and got.lines[0].text == "When you were here before"


def test_pick_rejects_another_recording_by_length():
    acoustic = _cand(title="Creep (Acoustic)", duration=259.0, synced=SYNC)  # 21 s off
    assert pick(Q, [acoustic]) is None
    live_same_title = _cand(duration=262.0, synced=SYNC)
    assert pick(Q, [live_same_title]) is None


def test_pick_timed_lyrics_a_few_seconds_off_become_plain_text():
    got = pick(Q, [_cand(duration=244.0, synced=SYNC, plain="words\nmore")])  # 6 s off
    assert got is not None and not got.synced and [x.text for x in got.lines] == ["words", "more"]


def test_pick_rejects_another_language_or_take_with_the_same_title_and_length():
    # the real case: "Idol" by YOASOBI came back with the English version's lyrics
    english = _cand(title="Idol (English Ver.)", artist="YOASOBI", duration=213.0, synced=SYNC)
    assert pick(Query("Idol", "YOASOBI", "Idol", 213), [english]) is None
    live = _cand(title="Creep - Live", duration=238.0, synced=SYNC)
    assert pick(Q, [live]) is None
    # and a live track doesn't take the studio lyrics (a different performance)
    assert pick(Query("Creep", "Radiohead", "x", 238, version="Live"), [_cand(synced=SYNC)]) is None


def test_pick_noise_in_brackets_is_not_a_different_recording():
    for title in ("Creep (Remastered 2011)", "Creep - 2009 Remaster", "Creep [Deluxe Edition]"):
        assert pick(Q, [_cand(title=title, synced=SYNC)]), title
    live_q = Query("Hurt (Live)", "Johnny Cash", "", 217)
    assert pick(
        live_q,
        [_cand(title="Hurt (Live at Folsom)", artist="Johnny Cash", duration=217.0, synced=SYNC)],
    )
    assert pick(
        Query("Creep", "Radiohead", "", 238, version="Remastered 2011"), [_cand(synced=SYNC)]
    )


def test_pick_rejects_another_song_and_another_artist():
    assert pick(Q, [_cand(title="Karma Police", synced=SYNC)]) is None
    assert pick(Q, [_cand(artist="Prince Rogers", synced=SYNC)]) is None


def test_pick_one_shared_artist_is_enough():
    q = Query("Get Lucky", "Daft Punk ft. Pharrell Williams", "RAM", 369)
    assert pick(q, [_cand(title="Get Lucky", artist="Daft Punk", duration=369.0, synced=SYNC)])


def test_pick_prefers_timed_then_the_closest_length():
    far = _cand(duration=240.0, synced="[00:01.00]far")
    near = _cand(duration=238.4, synced="[00:01.00]near")
    plain_only = _cand(duration=238.0, plain="plain")
    assert pick(Q, [plain_only, far, near]).lines[0].text == "near"


def test_pick_an_instrumental_is_reported_when_nothing_has_words():
    got = pick(
        Query("So What", "Miles Davis", "Kind of Blue", 562),
        [_cand(title="So What", artist="Miles Davis", duration=562.0, instrumental=True)],
    )
    assert got is not None and got.instrumental and got.lines == []


def test_pick_without_a_known_length_still_matches_by_name():
    assert pick(Query("Creep", "Radiohead"), [_cand(duration=None, plain="words")]) is not None


# ── the cache ────────────────────────────────────────────────────────────────


def test_cache_roundtrip_and_isrc_key():
    q = Query("Creep", "Radiohead", "Pablo Honey", 238, isrc="GBAYE9200070")
    assert lyrics_cache.load(q) is None
    lyrics_cache.save(q, Lyrics([Line("a", 1.0)], synced=True, source="X"))
    other_title = Query("Creep (2011)", "radiohead", "", 0, isrc="gbaye9200070")  # same ISRC
    got = lyrics_cache.load(other_title)
    assert got is not None and got.lyrics.lines == [Line("a", 1.0)] and got.lyrics.source == "X"


def test_cache_without_isrc_keys_on_the_folded_name_and_length():
    a = Query("Creep (Remastered)", "Radiohead", "", 238)
    b = Query("creep", "RADIOHEAD", "other album", 238)
    assert lyrics_cache.key(a) == lyrics_cache.key(b)
    assert lyrics_cache.key(a) != lyrics_cache.key(Query("creep", "radiohead", "", 259))


def test_cache_none_is_believed_for_a_week_found_always():
    q = Query("T", "A", "", 100)
    lyrics_cache.save(q, None)
    entry = lyrics_cache.load(q)
    assert entry.believed() and entry.believed(entry.fetched_at + 6 * 86400)
    assert not entry.believed(entry.fetched_at + 8 * 86400)
    lyrics_cache.save(q, Lyrics([Line("x")]))
    assert lyrics_cache.load(q).believed(time.time() + 365 * 86400)


def test_cache_keeps_the_readers_offset_across_a_new_answer():
    q = Query("T", "A", "", 100)
    lyrics_cache.save(q, Lyrics([Line("x", 1.0)], synced=True))
    lyrics_cache.update(q, offset=1.5)
    lyrics_cache.save(q, Lyrics([Line("y", 1.0)], synced=True))  # a fresher answer
    assert lyrics_cache.load(q).offset == 1.5


def test_cache_rejected_stays_none_and_is_never_asked_again():
    q = Query("T", "A", "", 100)
    lyrics_cache.save(q, Lyrics([Line("wrong", 1.0)], synced=True))
    lyrics_cache.update(q, reject=True)
    entry = lyrics_cache.load(q)
    assert entry.lyrics is None and entry.rejected
    assert entry.believed(time.time() + 365 * 86400)


def test_cache_files_are_private_and_a_bad_file_reads_as_empty():
    q = Query("T", "A", "", 100)
    lyrics_cache.save(q, Lyrics([Line("x")]))
    path = lyrics_cache._path(q)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    path.write_text("{not json")
    assert lyrics_cache.load(q) is None
    path.write_text(json.dumps({"version": 99, "fetched_at": 1, "lyrics": None}))
    assert lyrics_cache.load(q) is None  # another format: asked again


def test_cache_clear_erases_and_drops_saves_decided_before_it():
    q = Query("T", "A", "", 100)
    since = lyrics_cache.epoch()
    lyrics_cache.save(q, Lyrics([Line("x")]), since)
    assert lyrics_cache.clear() > 0 and lyrics_cache.load(q) is None
    lyrics_cache.save(q, Lyrics([Line("late")]), since)  # a worker still running at logout
    assert lyrics_cache.load(q) is None


# ── LRCLIB (mock transport) ──────────────────────────────────────────────────


def _lrclib(handler) -> tuple[LrcLib, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(base_url=LrcLib.BASE, transport=httpx.MockTransport(wrapped))
    return LrcLib(client), seen


def _row(**kw) -> dict:
    base = {
        "trackName": "Creep",
        "artistName": "Radiohead",
        "albumName": "Pablo Honey",
        "duration": 238.2,
        "syncedLyrics": "[00:10.00]words",
        "plainLyrics": "words",
        "instrumental": False,
    }
    return {**base, **kw}


def test_lrclib_exact_lookup_is_one_request_and_is_judged():
    provider, seen = _lrclib(lambda r: httpx.Response(200, json=_row()))
    got = asyncio.run(provider.find(Q))
    assert got.synced and got.source == "LRCLIB"
    assert len(seen) == 1 and seen[0].url.path == "/api/get"
    assert dict(seen[0].url.params) == {
        "track_name": "Creep",
        "artist_name": "Radiohead",
        "album_name": "Pablo Honey",
        "duration": "238",
    }


def test_lrclib_a_miss_falls_back_to_search_and_picks_the_right_recording():
    def handler(r):
        if r.url.path == "/api/get":
            return httpx.Response(404, json={"code": 404})
        return httpx.Response(
            200, json=[_row(duration=259.0), _row(duration=237.9, syncedLyrics="[00:01.00]right")]
        )

    provider, seen = _lrclib(handler)
    got = asyncio.run(provider.find(Q))
    assert got.lines[0].text == "right"
    assert [r.url.path for r in seen] == ["/api/get", "/api/search"]


def test_lrclib_a_wrong_exact_answer_is_not_used():
    def handler(r):
        return (
            httpx.Response(200, json=_row(trackName="Another Song"))
            if r.url.path == "/api/get"
            else httpx.Response(200, json=[])
        )

    provider, _ = _lrclib(handler)
    assert asyncio.run(provider.find(Q)) is None


def test_lrclib_without_album_or_length_searches_directly():
    provider, seen = _lrclib(lambda r: httpx.Response(200, json=[_row()]))
    assert asyncio.run(provider.find(Query("Creep", "Radiohead"))) is not None
    assert [r.url.path for r in seen] == ["/api/search"]


@pytest.mark.parametrize(
    "response", [httpx.Response(500), httpx.Response(429), httpx.Response(200, text="<html>")]
)
def test_lrclib_errors_are_provider_errors_not_none(response):
    provider, _ = _lrclib(lambda r: response)
    with pytest.raises(ProviderError):
        asyncio.run(provider.find(Q))


def test_lrclib_network_failure_is_a_provider_error():
    def boom(request):
        raise httpx.ConnectError("offline")

    provider, _ = _lrclib(boom)
    with pytest.raises(ProviderError):
        asyncio.run(provider.find(Q))


def test_lrclib_says_who_it_is():
    provider = LrcLib()
    ua = provider._http().headers["user-agent"]
    assert ua.startswith("qobit/")
    asyncio.run(provider.aclose())


# ── the service ──────────────────────────────────────────────────────────────


class Fake:
    name = "fake"

    def __init__(self, answer=None, error=False) -> None:
        self.answer, self.error, self.calls = answer, error, 0

    async def find(self, query):
        self.calls += 1
        await asyncio.sleep(0)
        if self.error:
            raise ProviderError("down")
        return self.answer

    async def aclose(self) -> None:
        pass


LY = Lyrics([Line("a", 1.0)], synced=True, source="fake")


def test_service_asks_once_then_the_cache_answers():
    fake = Fake(LY)
    service = LyricsService([fake])

    async def run():
        first = await service.lookup(Q)
        second = await service.lookup(Q)
        return first, second

    first, second = asyncio.run(run())
    assert first.status == second.status == "found" and fake.calls == 1


def test_service_a_failure_is_not_remembered_and_the_next_provider_is_tried():
    down, up = Fake(error=True), Fake(LY)
    assert asyncio.run(LyricsService([down, up]).lookup(Q)).status == "found"
    q2 = Query("Other", "Band", "", 99)
    only_down = LyricsService([Fake(error=True)])
    assert asyncio.run(only_down.lookup(q2)).status == "failed"
    assert lyrics_cache.load(q2) is None  # asked again next time


def test_service_nothing_found_is_none_and_remembered():
    fake = Fake(None)
    service = LyricsService([fake])
    assert asyncio.run(service.lookup(Q)).status == "none"
    assert asyncio.run(service.lookup(Q)).status == "none" and fake.calls == 1


def test_service_instrumental_status():
    fake = Fake(Lyrics(instrumental=True, source="fake"))
    assert asyncio.run(LyricsService([fake]).lookup(Q)).status == "instrumental"


def test_service_two_lookups_at_once_ask_once():
    fake = Fake(LY)
    service = LyricsService([fake])

    async def run():
        return await asyncio.gather(service.lookup(Q), service.lookup(Q))

    a, b = asyncio.run(run())
    assert a.status == b.status == "found" and fake.calls == 1


def test_service_reject_and_offset_are_remembered_with_the_answer():
    service = LyricsService([Fake(LY)])

    async def run():
        await service.lookup(Q)
        await service.set_offset(Q, -1.0)
        assert (await service.lookup(Q)).offset == -1.0
        await service.reject(Q)
        return await service.lookup(Q)

    again = asyncio.run(run())
    assert again.status == "none" and again.offset == -1.0


def test_service_with_no_providers_finds_nothing():
    assert asyncio.run(LyricsService([]).lookup(Q)).status == "none"


# ── what the review found ────────────────────────────────────────────────────


def test_lrclib_text_has_its_terminal_escapes_removed():
    """LRCLIB is user-submitted: an OSC 52 in a line must not reach the terminal."""
    evil = "[00:10.00]ok\x1b]52;c;ZXZpbA==\x07 done\x9b31m"
    provider, _ = _lrclib(lambda r: httpx.Response(200, json=_row(syncedLyrics=evil)))
    got = asyncio.run(provider.find(Q))
    assert "\x1b" not in got.lines[0].text and "\x07" not in got.lines[0].text
    assert "\x9b" not in got.lines[0].text


def test_a_part_number_is_a_different_song():
    q = Query("Echoes (Part 2)", "Radiohead", "", 245)
    other = _cand(title="Echoes (Part 1)", duration=249.0, plain="words")
    assert pick(q, [other]) is None
    assert pick(q, [_cand(title="Echoes (Part 2)", duration=245.0, plain="words")]) is not None


def test_service_a_cache_that_cannot_be_written_costs_the_cache_not_the_app(monkeypatch):
    def boom(*a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(lyrics_cache, "save", boom)
    monkeypatch.setattr(lyrics_cache, "update", boom)
    service = LyricsService([Fake(LY)])

    async def run():
        got = await service.lookup(Q)
        await service.set_offset(Q, 0.5)
        await service.reject(Q)
        return got

    assert asyncio.run(run()).status == "found"


def test_cache_an_offset_decided_before_an_erase_is_dropped():
    since = lyrics_cache.epoch()
    lyrics_cache.clear()
    lyrics_cache.update(Q, offset=1.0, since=since)
    assert lyrics_cache.load(Q) is None


def test_cache_a_choice_kept_before_the_isrc_was_known_is_found_with_it():
    """A queue restored from an older save has tracks without an ISRC; the same
    track from a fresh answer has one. Its offset and "wrong lyrics" stay."""
    bare = Query("Creep", "Radiohead", "Pablo Honey", 238)
    full = Query("Creep", "Radiohead", "Pablo Honey", 238, "GBAYE9200070")
    lyrics_cache.update(bare, offset=1.5)
    assert lyrics_cache.load(full).offset == 1.5
    lyrics_cache.update(full, reject=True)  # moves to the ISRC key
    assert lyrics_cache.load(full).rejected and lyrics_cache.load(full).offset == 1.5
    assert lyrics_cache.load(bare) is None  # no stale copy under the old key


def test_a_track_knows_whether_it_is_better_than_cd_quality():
    from qobit.qobuz.models import Track

    def track(depth, rate) -> Track:
        return Track.from_api(
            {
                "id": 1,
                "title": "t",
                "maximum_bit_depth": depth,
                "maximum_sampling_rate": rate,
                "performer": {"name": "a"},
            }
        )

    assert not track(16, 44.1).hires and not track(16, 48).hires
    assert track(24, 44.1).hires and track(16, 96).hires and track(24, 192).hires
    assert not Track.from_api({"id": 1, "title": "t", "performer": {"name": "a"}}).hires


def test_hires_follows_the_transports_rule():
    from qobit.qobuz.models import Track

    def track(depth, rate) -> Track:
        return Track(
            id="1", title="t", artist="a", album="b", album_id="1", duration=1,
            bit_depth=depth, sampling_rate=rate,
        )  # fmt: skip

    assert not track(16, 44.1).hires and not track(16, 48).hires  # CD and 48 kHz are not
    assert track(24, 44.1).hires and track(16, 96).hires
    assert not track(None, None).hires


def test_markup_escape_drops_a_trailing_backslash():
    from textual.content import Content

    from qobit.ui._utils import markup_escape

    ending_in_backslash = "Remix " + chr(92)
    assert (
        Content.from_markup(f"[italic]{markup_escape(ending_in_backslash)}[/italic]").plain
        == "Remix "
    )
