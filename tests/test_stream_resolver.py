"""StreamResolver: the quality fallback, preview detection, restrictions."""

from __future__ import annotations

import asyncio

import pytest

from qobit.qobuz.client import AuthExpiredError, QobuzError
from qobit.qobuz.models import NoStreamError
from qobit.qobuz.streams import StreamResolver


class FakeClient:
    """Answers get_streaming_url from a per-quality script; records calls."""

    def __init__(self, script: dict):
        self.script = script
        self.calls: list[str] = []

    async def get_streaming_url(self, track_id: str, quality: str) -> dict:
        self.calls.append(quality)
        answer = self.script[quality]
        if isinstance(answer, Exception):
            raise answer
        return answer


FLAC = {"url": "u", "format_id": 7, "sampling_rate": 96, "bit_depth": 24}
PREVIEW = {"url": "p", "format_id": 5, "sample": True, "sampling_rate": 44.1}
# What Qobuz sent for a track the account can't stream, not even as a
# preview: no URL, only a restriction code. The KeyError closed the app.
RESTRICTED = {
    "track_id": 31052309,
    "duration": 219,
    "restrictions": [{"code": "SampleRestrictedByRightHolders"}],
    "sampling_rate": 192,
    "bit_depth": 24,
}


def _resolver(script: dict) -> tuple[StreamResolver, FakeClient]:
    client = FakeClient(script)
    return StreamResolver(client), client  # type: ignore[arg-type]


def test_returns_the_first_working_quality_and_asks_it_first_next_time():
    resolver, client = _resolver({"FLAC_24_192": QobuzError("nope"), "FLAC_24_96": FLAC})
    stream = asyncio.run(resolver.resolve("42"))
    assert stream is not None and not stream.sample
    assert client.calls == ["FLAC_24_192", "FLAC_24_96"]
    client.calls.clear()
    asyncio.run(resolver.resolve("42"))
    assert client.calls == ["FLAC_24_96"]


def test_stops_at_a_preview_and_does_not_remember_it():
    # Qobuz returns the same preview at every quality: no point walking on.
    resolver, client = _resolver(
        {"FLAC_24_192": PREVIEW, "FLAC_24_96": PREVIEW, "FLAC_CD": PREVIEW}
    )
    stream = asyncio.run(resolver.resolve("42"))
    assert stream is not None and stream.sample
    assert client.calls == ["FLAC_24_192"]
    client.calls.clear()
    asyncio.run(resolver.resolve("42"))
    assert client.calls == ["FLAC_24_192"]  # not "remembered": same walk again


def test_none_when_every_quality_fails():
    err = QobuzError("nope")
    resolver, _ = _resolver({"FLAC_24_192": err, "FLAC_24_96": err, "FLAC_CD": err})
    assert asyncio.run(resolver.resolve("42")) is None


def test_raises_when_every_quality_is_restricted():
    resolver, client = _resolver(
        {"FLAC_24_192": RESTRICTED, "FLAC_24_96": RESTRICTED, "FLAC_CD": RESTRICTED}
    )
    with pytest.raises(NoStreamError, match="SampleRestrictedByRightHolders"):
        asyncio.run(resolver.resolve("42"))
    assert client.calls == ["FLAC_24_192", "FLAC_24_96", "FLAC_CD"]


def test_restricted_plus_failures_is_just_none():
    """A request failure might hide a playable quality: not "unavailable",
    so nothing skips on a flaky network."""
    resolver, _ = _resolver(
        {"FLAC_24_192": RESTRICTED, "FLAC_24_96": QobuzError("x"), "FLAC_CD": RESTRICTED}
    )
    assert asyncio.run(resolver.resolve("42")) is None


def test_falls_back_past_a_restricted_quality():
    resolver, _ = _resolver({"FLAC_24_192": RESTRICTED, "FLAC_24_96": FLAC})
    stream = asyncio.run(resolver.resolve("42"))
    assert stream is not None and stream.url == "u"


def test_auth_expired_propagates():
    resolver, _ = _resolver({"FLAC_24_192": AuthExpiredError("401")})
    with pytest.raises(AuthExpiredError):
        asyncio.run(resolver.resolve("42"))


def test_the_memory_is_bounded():
    resolver, _ = _resolver({"FLAC_24_192": FLAC})
    for i in range(1005):
        asyncio.run(resolver.resolve(str(i)))
    assert len(resolver._worked) == 1000
    assert "0" not in resolver._worked and "1004" in resolver._worked
