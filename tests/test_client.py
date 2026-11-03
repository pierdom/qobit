"""qobit.qobuz.client — the reverse-engineered bundle parsing, request
signing, and error mapping. No network calls: _derive_secrets/_sign are pure,
and the auth-guard tests only exercise the pre-flight check that now raises
before any request would be made.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib

import httpx
import pytest

from qobit.qobuz.client import AuthExpiredError, QobuzClient, QobuzError, _derive_secrets


def _make_bundle(secret: str, seed_tag: str = "a", timezone: str = "Berlin") -> str:
    """Build a synthetic bundle fragment matching the real one's shape closely
    enough for _derive_secrets' regexes: a seed/timezone pair plus a matching
    tz-info/extras pair, whose concatenation's *first* bytes (everything but
    a trailing 44-char suffix) base64-decode back to `secret`. The real
    format's trailing 44 chars are presumably a signature/hash we never
    verify — _derive_secrets just discards them via `combined[:-44]`."""
    secret_b64 = base64.standard_b64encode(secret.encode()).decode()
    padded = secret_b64 + ("x" * 44)
    seed, info, extras = padded[:10], padded[10:20], padded[20:]
    return (
        f'{seed_tag}.initialSeed("{seed}",window.utimezone.{timezone})'
        f'name:"Europe/{timezone}",info:"{info}",extras:"{extras}"'
    )


# ── _derive_secrets ──────────────────────────────────────────────────────────


def test_derive_secrets_extracts_known_secret_from_synthetic_bundle():
    bundle = _make_bundle("my-signing-secret")
    assert "my-signing-secret" in _derive_secrets(bundle)


def test_derive_secrets_matches_timezone_case_insensitively():
    """The bundle capitalises city names (Berlin) in both the seed-pair and
    the tz-info block consistently, but _derive_secrets explicitly lowercases
    both sides before matching — verify that still works, not just the
    already-matching-case default."""
    bundle = _make_bundle("secret-x", timezone="Abidjan")
    assert "secret-x" in _derive_secrets(bundle)


def test_derive_secrets_skips_seed_with_no_matching_timezone_info():
    bundle = 'a.initialSeed("AAAA",window.utimezone.Nowhere)'
    assert _derive_secrets(bundle) == []


def test_derive_secrets_empty_bundle_returns_empty_list():
    assert _derive_secrets("") == []


def test_derive_secrets_skips_undecodable_seed_without_raising():
    # info/extras missing entirely for this seed -> no tz match -> skipped,
    # no exception.
    bundle = 'a.initialSeed("!!!notb64!!!",window.utimezone.Berlin)'
    assert _derive_secrets(bundle) == []


# ── _sign ────────────────────────────────────────────────────────────────────


def test_sign_matches_documented_md5_formula(monkeypatch):
    client = QobuzClient()
    monkeypatch.setattr("qobit.qobuz.client.time.time", lambda: 1_700_000_000)
    sig, ts = client._sign("12345", 27, "the-secret")
    assert ts == 1_700_000_000
    raw = "trackgetFileUrlformat_id27intentstreamtrack_id123451700000000the-secret"
    assert sig == hashlib.md5(raw.encode()).hexdigest()


def test_sign_differs_for_different_secrets(monkeypatch):
    client = QobuzClient()
    monkeypatch.setattr("qobit.qobuz.client.time.time", lambda: 1_700_000_000)
    sig_a, _ = client._sign("1", 6, "secret-a")
    sig_b, _ = client._sign("1", 6, "secret-b")
    assert sig_a != sig_b


# ── _raise_for_status ────────────────────────────────────────────────────────


def test_raise_for_status_ok_response_does_not_raise():
    r = httpx.Response(200, json={"ok": True})
    QobuzClient._raise_for_status(r)  # should not raise


def test_raise_for_status_401_raises_auth_expired():
    r = httpx.Response(401, json={"message": "Invalid token"})
    with pytest.raises(AuthExpiredError, match="Invalid token"):
        QobuzClient._raise_for_status(r)


def test_raise_for_status_other_error_raises_qobuz_error_not_auth_expired():
    r = httpx.Response(400, json={"message": "Bad request"})
    with pytest.raises(QobuzError) as exc_info:
        QobuzClient._raise_for_status(r)
    assert not isinstance(exc_info.value, AuthExpiredError)


def test_raise_for_status_falls_back_to_raw_text_when_not_json():
    r = httpx.Response(500, text="<html>oops</html>")
    with pytest.raises(QobuzError, match="oops"):
        QobuzClient._raise_for_status(r)


# ── auth guard (regression test: this used to be a bare `assert`) ───────────


def test_unauthenticated_client_raises_qobuz_error_not_assertion_error():
    """_base_params previously used `assert self._app_id, "..."`, which (a)
    raises AssertionError — a type most call sites don't special-case — and
    (b) silently no-ops under `python -O`. Pin it to a real QobuzError."""
    client = QobuzClient()
    with pytest.raises(QobuzError, match="Not authenticated"):
        asyncio.run(client.get_all_favorite_tracks())


def test_get_streaming_url_without_login_raises_qobuz_error():
    client = QobuzClient()
    with pytest.raises(QobuzError, match="Not authenticated"):
        asyncio.run(client.get_streaming_url("123"))


def test_get_streaming_url_with_app_id_but_no_token_still_raises():
    client = QobuzClient(app_id="12345")
    with pytest.raises(QobuzError, match="Not authenticated"):
        asyncio.run(client.get_streaming_url("123"))


# ── restore_session / repr ───────────────────────────────────────────────────


def test_restore_session_sets_app_id_token_and_secrets():
    client = QobuzClient()
    client.restore_session("app1", "tok1", ["s1", "s2"])
    assert client._app_id == "app1"
    assert client.user_auth_token == "tok1"
    assert client._secret_candidates == ["s1", "s2"]


def test_repr_does_not_leak_the_token_value():
    client = QobuzClient()
    client.restore_session("app1", "super-secret-token", [])
    assert "super-secret-token" not in repr(client)
    assert "chars" in repr(client)
