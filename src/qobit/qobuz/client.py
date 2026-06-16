# Lifted from pierdom/qobuz-mcp — QobuzClient has no MCP dependencies.
# Added: async context manager (__aenter__/__aexit__), OAuth login flow.

import asyncio
import base64
import hashlib
import re
import socket
import time
import webbrowser
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

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


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def _wait_for_oauth_code(port: int, timeout: float = 120.0) -> str:
    """Starts a temporary asyncio HTTP server; returns the OAuth code from the redirect."""
    loop = asyncio.get_event_loop()
    code_future: asyncio.Future[str] = loop.create_future()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            request_line = data.decode(errors="replace").split("\r\n")[0]
            parts = request_line.split(" ")
            path = parts[1] if len(parts) > 1 else "/"
            qs = parse_qs(urlparse(path).query)
            code = (qs.get("code_autorisation") or qs.get("code") or [None])[0]
            body = (
                b"<html><body><h2>Login successful! You can close this tab.</h2></body></html>"
                if code
                else b"<html><body><h2>Processing\xe2\x80\xa6</h2></body></html>"
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            await writer.drain()
            if code and not code_future.done():
                code_future.set_result(code)
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(_handle, "localhost", port)
    async with server:
        try:
            return await asyncio.wait_for(code_future, timeout=timeout)
        except asyncio.TimeoutError:
            raise QobuzError("OAuth login timed out (2 minutes)")


class QobuzError(Exception):
    pass


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
        self._http = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> "QobuzClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def login(self, email: str, password: str) -> None:
        bundle_app_ids = await self._fetch_bundle_credentials()
        pwd_md5 = hashlib.md5(password.encode()).hexdigest()

        candidates = (
            [self._app_id] + [i for i in bundle_app_ids if i != self._app_id]
            if self._app_id
            else bundle_app_ids
        )

        last_err: QobuzError | None = None
        for app_id in candidates:
            self._app_id = app_id
            try:
                data = await self._get("user/login", username=email, password=pwd_md5)
            except QobuzError:
                try:
                    data = await self._get("user/login", username=email, password=password)
                except QobuzError as e2:
                    last_err = e2
                    continue
            self.user_auth_token = data["user_auth_token"]
            return

        raise last_err or QobuzError("No working app_id found in Qobuz bundle")

    async def _fetch_bundle_credentials(self) -> list[str]:
        r = await self._http.get(f"{PLAY_BASE}/login")
        r.raise_for_status()

        bundle_match = _BUNDLE_RE.search(r.text)
        if not bundle_match:
            raise QobuzError("Cannot locate Qobuz bundle script in login page")

        r = await self._http.get(PLAY_BASE + bundle_match.group(1))
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
        assert self._app_id, "app_id not resolved — call login() first"
        params: dict[str, Any] = {"app_id": self._app_id}
        if self.user_auth_token:
            params["user_auth_token"] = self.user_auth_token
        return params

    async def _get(self, endpoint: str, **params: Any) -> dict:
        p = self._base_params()
        p.update(params)
        r = await self._http.get(API_BASE + endpoint, params=p)
        self._raise_for_status(r)
        return r.json()

    async def _post(self, endpoint: str, **data: Any) -> dict:
        d = self._base_params()
        d.update(data)
        r = await self._http.post(API_BASE + endpoint, data=d)
        self._raise_for_status(r)
        return r.json()

    @staticmethod
    def _raise_for_status(r: httpx.Response) -> None:
        if r.is_error:
            try:
                msg = r.json().get("message", r.text)
            except Exception:
                msg = r.text
            raise QobuzError(f"Qobuz API error {r.status_code}: {msg}")

    def _sign(self, track_id: str, format_id: int, secret: str) -> tuple[str, int]:
        ts = int(time.time())
        raw = f"trackgetFileUrlformat_id{format_id}intentstreamtrack_id{track_id}{ts}{secret}"
        return hashlib.md5(raw.encode()).hexdigest(), ts

    def restore_session(self, app_id: str, token: str, secrets: list[str]) -> None:
        """Restore a previously saved OAuth session without re-authenticating."""
        self._app_id = app_id
        self.user_auth_token = token
        self._secret_candidates = secrets + [
            s for s in self._secret_candidates if s not in secrets
        ]

    async def login_oauth(self) -> None:
        """Browser-based OAuth flow; sets user_auth_token with streaming permissions."""
        await self._fetch_bundle_credentials()

        app_id = self._prod_app_id or "798273057"
        private_key = self._private_key
        if not private_key:
            raise QobuzError("Cannot extract privateKey from bundle — OAuth unavailable")

        port = _find_free_port()
        redirect_url = f"http://localhost:{port}"
        oauth_url = (
            f"https://www.qobuz.com/signin/oauth"
            f"?ext_app_id={app_id}&redirect_url={redirect_url}"
        )

        print("\nOpening Qobuz login in browser...")
        print(f"If it doesn't open automatically: {oauth_url}\n")
        webbrowser.open(oauth_url)

        code = await _wait_for_oauth_code(port)

        r = await self._http.get(
            API_BASE + "oauth/callback",
            params={"code": code, "private_key": private_key},
            headers={"X-App-Id": app_id},
        )
        self._raise_for_status(r)
        interim_token = r.json().get("token")
        if not interim_token:
            raise QobuzError(f"OAuth callback returned no token: {r.json()}")

        r = await self._http.post(
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
        return await self._get("album/get", album_id=album_id, extra="tracks")

    async def get_track(self, track_id: str) -> dict:
        return await self._get("track/get", track_id=track_id)

    async def get_artist(self, artist_id: str, albums_limit: int = 20) -> dict:
        return await self._get(
            "artist/get",
            artist_id=artist_id,
            extra="albums",
            albums_limit=albums_limit,
            albums_sort="release_date",
        )

    async def get_streaming_url(self, track_id: str, quality: str = "FLAC_CD") -> dict:
        format_id = QUALITY_IDS.get(quality, QUALITY_IDS["FLAC_CD"])
        candidates = self._secret_candidates or [self._app_secret or ""]
        last_error: QobuzError | None = None

        assert self._app_id, "app_id not resolved — call login() or login_oauth() first"
        assert self.user_auth_token, "not logged in — call login() or login_oauth() first"

        for secret in candidates:
            sig, ts = self._sign(track_id, format_id, secret)
            try:
                # Auth via headers so app_id/token are excluded from genSignedRequest's
                # sorted-param hash (which only covers the URL query params).
                r = await self._http.get(
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

    # --- user library ---

    async def get_user_favorites(self, type: str = "tracks", limit: int = 50) -> dict:
        return await self._get("favorite/getUserFavorites", type=type, limit=limit)

    async def get_user_playlists(self, limit: int = 50) -> dict:
        return await self._get("playlist/getUserPlaylists", limit=limit)

    async def get_playlist(self, playlist_id: str) -> dict:
        return await self._get(
            "playlist/get",
            playlist_id=playlist_id,
            extra="tracks",
            limit=500,
        )

    # --- playlist write ---

    async def create_playlist(
        self,
        name: str,
        description: str = "",
        is_public: bool = False,
        track_ids: list[str] | None = None,
    ) -> dict:
        result = await self._post(
            "playlist/create",
            name=name,
            description=description,
            is_public="true" if is_public else "false",
        )
        if track_ids:
            await self.add_tracks_to_playlist(str(result["id"]), track_ids)
        return result

    async def update_playlist(
        self,
        playlist_id: str,
        name: str | None = None,
        description: str | None = None,
        is_public: bool | None = None,
    ) -> dict:
        data: dict[str, Any] = {"playlist_id": playlist_id}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if is_public is not None:
            data["is_public"] = "true" if is_public else "false"
        return await self._post("playlist/update", **data)

    async def add_tracks_to_playlist(self, playlist_id: str, track_ids: list[str]) -> dict:
        return await self._post(
            "playlist/addTracks",
            playlist_id=playlist_id,
            track_ids=",".join(str(t) for t in track_ids),
        )

    async def remove_tracks_from_playlist(
        self, playlist_id: str, playlist_track_ids: list[str]
    ) -> dict:
        return await self._post(
            "playlist/deleteTracks",
            playlist_id=playlist_id,
            playlist_track_ids=",".join(str(t) for t in playlist_track_ids),
        )
