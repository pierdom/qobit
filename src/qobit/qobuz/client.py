# Lifted from pierdom/qobuz-mcp — QobuzClient has no MCP dependencies.
# Added: async context manager (__aenter__/__aexit__), OAuth login flow.

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import sys
import time
import webbrowser
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import httpx

from .oauth import LoginError, LoginPrompt, redirect_server

API_BASE = "https://www.qobuz.com/api.json/0.2/"
PLAY_BASE = "https://play.qobuz.com"

QUALITY_IDS = {
    "MP3_320": 5,
    "FLAC_CD": 6,
    "FLAC_24_96": 7,
    "FLAC_24_192": 27,
}

# Timezone names in bundle are capitalized (Berlin, Abidjan, London) — must be [a-zA-Z_]+
_BUNDLE_RE = re.compile(r'<script src="(/resources/[^"]+/bundle\.js)"')
_ALL_APP_IDS_RE = re.compile(r'appId:"(\d+)"')
_SEED_TZ_RE = re.compile(r'[a-z]\.initialSeed\("([\w=]+)",window\.utimezone\.([a-zA-Z_]+)\)')
_TZ_INFO_RE = re.compile(r'name:"\w+/([a-zA-Z_]+)",info:"([\w=]+)",extras:"([\w=]+)"')
_PRIVATE_KEY_RE = re.compile(r'privateKey:\s*"([A-Za-z0-9]{6,30})"')
_PROD_APP_ID_RE = re.compile(r'production:\{api:\{appId:"(\d+)"')


def _derive_secrets(bundle: str) -> list[str]:
    """Extract signing secrets via the TZ-seed obfuscation in the bundle."""
    seed_tz_pairs = _SEED_TZ_RE.findall(bundle)
    # Build lookup with lowercase keys; bundle uses capitalized city names
    tz_info = {name.lower(): (info, extras) for name, info, extras in _TZ_INFO_RE.findall(bundle)}

    secrets: list[str] = []
    for seed, timezone in seed_tz_pairs:
        key = timezone.lower()
        if key not in tz_info:
            continue
        info, extras = tz_info[key]
        try:
            combined = seed + info + extras
            decoded = base64.standard_b64decode(combined[:-44]).decode("utf-8")
            if decoded:
                secrets.append(decoded)
        except Exception:
            pass

    return secrets


def _has_graphical_browser() -> bool:
    """Whether opening a browser here can work. Without a display (SSH, a
    console) Python's webbrowser falls back to a text browser such as lynx,
    which takes over the terminal; the printed link is the way in there."""
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _print_login(prompt: LoginPrompt) -> None:
    print("\nLog in to Qobuz by opening this link in a browser:\n")
    print(f"  {prompt.url}\n")
    print("Waiting for the login to finish...", flush=True)


class QobuzError(Exception):
    pass


class AuthExpiredError(QobuzError):
    """Raised when the Qobuz session token is no longer valid (HTTP 401)."""


class QobuzClient:
    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
    ):
        self._app_id = app_id
        self._app_secret = app_secret
        self._secret_candidates: list[str] = [app_secret] if app_secret else []
        self.user_auth_token: str | None = None
        self._private_key: str | None = None
        self._prod_app_id: str | None = None
        self._http = httpx.AsyncClient(timeout=30.0, verify=True)
        # Session-scoped LRU caches — avoids re-fetching the same album/artist
        # detail when the user navigates back to an already-visited page. Bounded
        # to prevent unbounded growth in very long sessions.
        self._album_cache: OrderedDict[str, dict] = OrderedDict()
        self._artist_detail_cache: OrderedDict[str, dict] = OrderedDict()
        # artist/page feeds Top Tracks, the header's similar-artists preview and
        # the similar panel: one request per artist, shared even while in flight.
        self._artist_page_cache: OrderedDict[str, asyncio.Future] = OrderedDict()

    def __repr__(self) -> str:
        token = self.user_auth_token
        token_repr = f"<set, {len(token)} chars>" if token else "None"
        return f"QobuzClient(app_id={self._app_id!r}, user_auth_token={token_repr})"

    async def __aenter__(self) -> "QobuzClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def _fetch_bundle_credentials(self) -> list[str]:
        r = await self._send("GET", f"{PLAY_BASE}/login")
        r.raise_for_status()

        bundle_match = _BUNDLE_RE.search(r.text)
        if not bundle_match:
            raise QobuzError("Cannot locate Qobuz bundle script in login page")

        r = await self._send("GET", PLAY_BASE + bundle_match.group(1))
        r.raise_for_status()
        bundle = r.text

        all_ids = list(dict.fromkeys(_ALL_APP_IDS_RE.findall(bundle)))
        if not all_ids:
            raise QobuzError("Cannot extract app_id from Qobuz bundle")

        candidates = _derive_secrets(bundle)
        self._secret_candidates = candidates + [
            s for s in self._secret_candidates if s not in candidates
        ]

        pk_match = _PRIVATE_KEY_RE.search(bundle)
        if pk_match:
            self._private_key = pk_match.group(1)

        prod_match = _PROD_APP_ID_RE.search(bundle)
        if prod_match:
            self._prod_app_id = prod_match.group(1)

        return all_ids

    def _base_params(self) -> dict[str, Any]:
        # Raised as QobuzError (not `assert`) so every call site's existing
        # `except QobuzError` already shows a clean message — `assert` would
        # both raise the wrong exception type for callers expecting QobuzError
        # and silently vanish under `python -O`.
        if not self._app_id:
            raise QobuzError("Not authenticated — run: qobit auth")
        params: dict[str, Any] = {"app_id": self._app_id}
        if self.user_auth_token:
            params["user_auth_token"] = self.user_auth_token
        return params

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Every HTTP call goes through here: network failures (offline,
        timeouts, dropped connections) become QobuzError, which callers
        handle. Raw httpx errors used to escape them, and a worker that
        raises closes the app, so a search while offline quit qobit."""
        try:
            return await self._http.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            raise QobuzError(f"Network error: {e or type(e).__name__}") from e

    async def _get(self, endpoint: str, **params: Any) -> dict:
        p = self._base_params()
        p.update(params)
        r = await self._send("GET", API_BASE + endpoint, params=p)
        self._raise_for_status(r)
        return r.json()

    async def _post(self, endpoint: str, **data: Any) -> dict:
        d = self._base_params()
        d.update(data)
        r = await self._send("POST", API_BASE + endpoint, data=d)
        self._raise_for_status(r)
        return r.json()

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.is_error:
            try:
                msg = r.json().get("message", r.text)
            except Exception:
                msg = r.text
            if r.status_code == 401:
                raise AuthExpiredError(f"Qobuz session expired ({r.status_code}): {msg}")
            raise QobuzError(f"Qobuz API error {r.status_code}: {msg}")

    def _sign(self, track_id: str, format_id: int, secret: str) -> tuple[str, int]:
        ts = int(time.time())
        raw = f"trackgetFileUrlformat_id{format_id}intentstreamtrack_id{track_id}{ts}{secret}"
        return hashlib.md5(raw.encode()).hexdigest(), ts

    def restore_session(self, app_id: str, token: str, secrets: list[str]) -> None:
        """Restore a previously saved OAuth session without re-authenticating."""
        self._app_id = app_id
        self.user_auth_token = token
        self._secret_candidates = secrets + [s for s in self._secret_candidates if s not in secrets]

    async def login_oauth(self, show_url: Callable[[LoginPrompt], None] | None = None) -> None:
        """Browser-based OAuth flow; sets user_auth_token with streaming
        permissions. ``show_url`` receives the ``LoginPrompt`` (its ``url``,
        and ``submit()`` for a pasted address) before anything else happens,
        so the link is always shown: no browser here, SSH, a browser that
        didn't come up. Defaults to printing it. See ``qobuz/oauth.py``."""
        await self._fetch_bundle_credentials()

        app_id = self._prod_app_id or "798273057"
        private_key = self._private_key
        if not private_key:
            raise QobuzError("Cannot extract privateKey from bundle — OAuth unavailable")

        try:
            async with redirect_server(app_id) as prompt:
                (show_url or _print_login)(prompt)
                if _has_graphical_browser():
                    with contextlib.suppress(Exception):
                        webbrowser.open(prompt.url)
                code = await prompt.wait()
        except LoginError as e:
            raise QobuzError(str(e)) from None

        r = await self._send(
            "GET",
            API_BASE + "oauth/callback",
            params={"code": code, "private_key": private_key},
            headers={"X-App-Id": app_id},
        )
        try:
            self._raise_for_status(r)
        except QobuzError as e:
            raise QobuzError(
                "Qobuz didn't accept the login code (expired, already used, or "
                f"mistyped): run qobit auth again. ({e})"
            ) from None
        interim_token = r.json().get("token")
        if not interim_token:
            raise QobuzError(f"OAuth callback returned no token: {r.json()}")

        r = await self._send(
            "POST",
            API_BASE + "user/login",
            content=b"extra=partner",
            headers={
                "X-App-Id": app_id,
                "X-User-Auth-Token": interim_token,
                "Content-Type": "text/plain;charset=UTF-8",
            },
        )
        self._raise_for_status(r)
        data = r.json()
        self.user_auth_token = data.get("user_auth_token")
        if not self.user_auth_token:
            raise QobuzError(f"OAuth session exchange returned no token: {data}")

        self._app_id = app_id

    # --- catalogue ---

    async def search(self, query: str, type: str = "tracks", limit: int = 20) -> dict:
        return await self._get("catalog/search", query=query, type=type, limit=limit)

    async def get_album(self, album_id: str) -> dict:
        if album_id in self._album_cache:
            self._album_cache.move_to_end(album_id)
            return self._album_cache[album_id]
        result = await self._get("album/get", album_id=album_id)
        self._album_cache[album_id] = result
        if len(self._album_cache) > 500:
            self._album_cache.popitem(last=False)
        return result

    async def get_track(self, track_id: str) -> dict:
        return await self._get("track/get", track_id=track_id)

    async def get_dynamic_suggestions(
        self, listened_track_ids: list[int], analysed: list[dict], limit: int = 50
    ) -> dict:
        """Qobuz "song radio": similar-track recommendations seeded from recently
        listened tracks. POST dynamic/suggest with a JSON body (sent as
        text/plain, like the OAuth login exchange). Response shape:
        ``{"tracks": {"items": [<track objects>]}}`` — items parse via
        ``Track.from_api``. Field names (``listened_tracks_ids`` /
        ``track_to_analysed``) match the web client exactly."""
        body = {
            "limit": limit,
            "listened_tracks_ids": listened_track_ids,
            "track_to_analysed": analysed,
        }
        r = await self._send(
            "POST",
            API_BASE + "dynamic/suggest",
            params=self._base_params(),
            content=json.dumps(body),
            headers={"Content-Type": "text/plain;charset=UTF-8"},
        )
        self._raise_for_status(r)
        return r.json()

    async def get_artist(self, artist_id: str, albums_limit: int = 20) -> dict:
        return await self._get(
            "artist/get",
            artist_id=artist_id,
            extra="albums",
            albums_limit=albums_limit,
            albums_sort="release_date",
        )

    async def get_artist_detail(self, artist_id: str) -> dict:
        """Biography, image, and every album from artist/get (no top tracks)."""
        if artist_id in self._artist_detail_cache:
            self._artist_detail_cache.move_to_end(artist_id)
            return self._artist_detail_cache[artist_id]
        result = await self._get_paged(
            "artist/get",
            "albums",
            500,
            artist_id=artist_id,
            extra="albums",
            albums_sort="release_date",
        )
        self._artist_detail_cache[artist_id] = result
        if len(self._artist_detail_cache) > 200:
            self._artist_detail_cache.popitem(last=False)
        return result

    async def get_artist_page(self, artist_id: str) -> dict:
        """artist/page (top tracks, a similar-artists preview, …), cached."""
        future = self._artist_page_cache.get(artist_id)
        if future is None:
            future = asyncio.ensure_future(self._get("artist/page", artist_id=artist_id))
            self._artist_page_cache[artist_id] = future
            if len(self._artist_page_cache) > 200:
                self._artist_page_cache.popitem(last=False)
        try:
            # shield: a caller's cancelled worker mustn't cancel the shared request
            return await asyncio.shield(future)
        except QobuzError:
            self._artist_page_cache.pop(artist_id, None)  # retry next time
            raise

    async def get_artist_top_tracks(self, artist_id: str) -> list[dict]:
        """Popularity-ranked top tracks from artist/page."""
        page = await self.get_artist_page(artist_id)
        top_tracks = page.get("top_tracks") or []
        return top_tracks if isinstance(top_tracks, list) else top_tracks.get("items") or []

    async def get_similar_artists_preview(self, artist_id: str) -> tuple[list[dict], bool]:
        """The few similar artists artist/page includes (``{"id", "name"}``),
        and whether there are more."""
        sim = (await self.get_artist_page(artist_id)).get("similar_artists") or {}
        items = []
        for it in sim.get("items") or []:
            name = it.get("name")
            name = name.get("display", "") if isinstance(name, dict) else name or ""
            items.append({"id": str(it["id"]), "name": name})
        return items, bool(sim.get("has_more"))

    async def get_similar_artists(self, artist_id: str) -> list[dict]:
        """Every similar artist (artist/getSimilarArtists, paged)."""
        first = await self._get_paged(
            "artist/getSimilarArtists", "artists", 100, artist_id=artist_id
        )
        return (first.get("artists") or {}).get("items") or []

    async def get_streaming_url(self, track_id: str, quality: str = "FLAC_CD") -> dict:
        format_id = QUALITY_IDS.get(quality, QUALITY_IDS["FLAC_CD"])
        candidates = self._secret_candidates or [self._app_secret or ""]
        last_error: QobuzError | None = None

        if not self._app_id or not self.user_auth_token:
            raise QobuzError("Not authenticated — run: qobit auth")

        for secret in candidates:
            sig, ts = self._sign(track_id, format_id, secret)
            try:
                # Auth via headers so app_id/token are excluded from genSignedRequest's
                # sorted-param hash (which only covers the URL query params).
                r = await self._send(
                    "GET",
                    API_BASE + "track/getFileUrl",
                    params={
                        "track_id": track_id,
                        "format_id": format_id,
                        "intent": "stream",
                        "request_ts": ts,
                        "request_sig": sig,
                    },
                    headers={
                        "X-App-Id": self._app_id,
                        "X-User-Auth-Token": self.user_auth_token,
                    },
                )
                self._raise_for_status(r)
                result = r.json()
                self._app_secret = secret
                self._secret_candidates = [secret] + [
                    s for s in self._secret_candidates if s != secret
                ]
                return result
            except QobuzError as e:
                last_error = e

        raise last_error or QobuzError("No valid app_secret found for streaming")

    async def create_connect_token(self) -> tuple[str, str]:
        """Mint a Qobuz Connect socket token: ``(endpoint, jwt)``. One token
        per socket — the cloud evicts a second socket using the same one."""
        if not self._app_id or not self.user_auth_token:
            raise QobuzError("Not authenticated — run: qobit auth")
        r = await self._send(
            "POST",
            API_BASE + "qws/createToken",
            data={"jwt": "jwt_qws"},
            headers={"X-App-Id": self._app_id, "X-User-Auth-Token": self.user_auth_token},
        )
        self._raise_for_status(r)
        tok = (r.json() or {}).get("jwt_qws") or {}
        if not tok.get("jwt") or not tok.get("endpoint"):
            raise QobuzError("Connect token missing from qws/createToken response")
        return tok["endpoint"], tok["jwt"]

    # --- user library ---

    async def set_favorite(self, kind: str, item_id: str, on: bool) -> dict:
        """Add or remove a favourite. ``kind`` is "track", "album" or "artist"
        (a favourite artist is what the apps call following)."""
        endpoint = "favorite/create" if on else "favorite/delete"
        return await self._get(endpoint, **{f"{kind}_ids": item_id})

    async def _get_paged(self, endpoint: str, key: str, page: int, **params: Any) -> dict:
        """GET ``endpoint`` and fill in every page of the list it embeds under
        ``key`` (``{"items": [...], "total": n}``): the first page, then the
        rest in parallel, a few requests at a time. Returns the first response
        with the complete list. Endpoints cap their lists (an artist's albums,
        a playlist's tracks), so a single call silently truncates."""
        first = await self._get(endpoint, limit=page, offset=0, **params)
        data = first.get(key) or {}
        items: list[dict] = list(data.get("items") or [])
        total: int = data.get("total") or 0
        if total > len(items):
            gate = asyncio.Semaphore(4)

            async def fetch(offset: int) -> list[dict]:
                async with gate:
                    r = await self._get(endpoint, limit=page, offset=offset, **params)
                return (r.get(key) or {}).get("items") or []

            for chunk in await asyncio.gather(*[fetch(o) for o in range(page, total, page)]):
                items.extend(chunk)
            data["items"] = items
        return first

    async def _get_all_favorites(self, kind: str) -> list[dict]:
        """Every favourite of one kind ("tracks", "albums", "artists")."""
        first = await self._get_paged("favorite/getUserFavorites", kind, 50, type=kind)
        return (first.get(kind) or {}).get("items") or []

    async def get_all_favorite_tracks(self) -> list[dict]:
        return await self._get_all_favorites("tracks")

    async def get_all_favorite_albums(self) -> list[dict]:
        return await self._get_all_favorites("albums")

    async def get_all_favorite_artists(self) -> list[dict]:
        return await self._get_all_favorites("artists")

    async def get_all_user_playlists(self) -> list[dict]:
        first = await self._get_paged("playlist/getUserPlaylists", "playlists", 50)
        return (first.get("playlists") or {}).get("items") or []

    async def get_playlist(self, playlist_id: str) -> dict:
        """A playlist with all its tracks (one call stops at 500)."""
        return await self._get_paged(
            "playlist/get", "tracks", 500, playlist_id=playlist_id, extra="tracks"
        )
