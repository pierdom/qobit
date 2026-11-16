"""The browser half of the Qobuz OAuth login: the link, and the code it brings back.

Qobuz sends the browser back to the ``redirect_url`` it was given with
``?code_autorisation=…`` appended: ``http://localhost:<port>/<secret>?code_autorisation=…``.
That code reaches qobit one of two ways, whichever comes first:

- the local redirect server (the browser runs on this machine), or
- the user pasting the address the browser ended on (it logged in on another
  device: SSH, a headless box; that page fails to load, but its address
  carries the code). ``LoginPrompt.submit`` takes it; UI-agnostic, so the CLI
  feeds it from stdin and a TUI dialog can feed it from an Input.

The server binds the loopback interfaces only, on a port the OS picks at bind
time (no probe-then-bind race), and takes one code, only on ``GET
/<secret>``. The secret path is random per login and stands in for OAuth's
``state``, which Qobuz drops (verified: the redirect carries only
``code_autorisation``). Without it, any web page or local process that found
the port could hand qobit a code for *its own* account (login CSRF). A pasted
address must carry the same path.
"""

import asyncio
import contextlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from ..secret import Secret

OAUTH_URL = "https://www.qobuz.com/signin/oauth"
LOGIN_TIMEOUT = 300.0  # long enough to log in on another device and paste back

# Printable ASCII, bounded: the code is only ever sent back to Qobuz as a
# query parameter, but nothing else has any business arriving in its place.
_CODE_RE = re.compile(r"[\x21-\x7e]{1,1024}")

_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'><title>qobit</title></head>"
    "<body style='font-family:system-ui,sans-serif;margin:3em'><h2>{}</h2><p>{}</p></body></html>"
)


class LoginError(Exception):
    """The login can't finish (cancelled on Qobuz, timed out)."""


@dataclass
class LoginPrompt:
    """One login attempt: the link to open and the place its code lands."""

    url: str
    port: int
    path: str  # "/<secret>": the only path a code is taken on
    _code: asyncio.Future = field(repr=False)

    @property
    def done(self) -> bool:
        return self._code.done()

    def submit(self, text: str) -> str:
        """Feed the address the browser ended on (pasted by the user). Returns
        "ok", "no_code" (not a login address: try again), "wrong_login" (an
        address from another login), "error" (Qobuz sent an error: the login
        fails) or "done" (already finished)."""
        parsed = urlparse(text.strip())
        if not parsed.query:
            return "no_code"
        return self._accept(parsed.path, parse_qs(parsed.query))

    def _accept(self, path: str, qs: dict[str, list[str]]) -> str:
        if self._code.done():
            return "done"
        if not secrets.compare_digest(path.encode(), self.path.encode()):
            return "wrong_login"
        if error := (qs.get("error") or [None])[0]:
            self._code.set_exception(LoginError(f"Qobuz refused the login: {error}"))
            return "error"
        code = (qs.get("code_autorisation") or qs.get("code") or [None])[0]
        if not code or not _CODE_RE.fullmatch(code):
            return "no_code"
        self._code.set_result(Secret(code))
        return "ok"

    async def wait(self, timeout: float | None = None) -> str:
        timeout = LOGIN_TIMEOUT if timeout is None else timeout
        try:
            return await asyncio.wait_for(asyncio.shield(self._code), timeout)
        except TimeoutError:
            minutes = round(timeout / 60)
            raise LoginError(f"Login timed out ({minutes} minutes)") from None


_REPLIES = {
    "ok": ("200 OK", "Logged in to qobit", "You can close this tab."),
    "done": ("200 OK", "Already logged in", "You can close this tab."),
    "error": ("200 OK", "Login cancelled", "Run qobit auth to try again."),
    "no_code": ("400 Bad Request", "No login code", "Open the qobit login link to log in."),
}


async def _reply(writer: asyncio.StreamWriter, status: str, title: str, text: str) -> None:
    body = _PAGE.format(title, text).encode()
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
        "Connection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()


def _handler(prompt: LoginPrompt) -> Callable:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            method, target, *_ = [*line.decode("latin-1").split(" "), "", ""]
            if method != "GET":
                await _reply(writer, "405 Method Not Allowed", "Not allowed", "")
                return
            parsed = urlparse(target)
            outcome = prompt._accept(parsed.path, parse_qs(parsed.query))
            if outcome == "wrong_login":  # favicon.ico, a guess, another login
                await _reply(writer, "404 Not Found", "Not found", "")
                return
            await _reply(writer, *_REPLIES.get(outcome, _REPLIES["no_code"]))
        except Exception:
            pass
        finally:
            writer.close()

    return handle


@contextlib.asynccontextmanager
async def redirect_server(app_id: str):
    """Listen on loopback for the redirect; yields the ``LoginPrompt``.

    Binds 127.0.0.1 on a port the OS picks, then ::1 on the same port when
    the host has IPv6 (browsers may resolve ``localhost`` to either)."""
    loop = asyncio.get_running_loop()
    path = "/" + secrets.token_urlsafe(16)  # [A-Za-z0-9_-]: path-safe as is
    future: asyncio.Future[str] = loop.create_future()
    servers: list[asyncio.Server] = []
    prompt: LoginPrompt | None = None

    def handle(reader, writer):  # bound once prompt exists
        return _handler(prompt)(reader, writer)

    try:
        v4 = await asyncio.start_server(handle, "127.0.0.1", 0)
        servers.append(v4)
        port = v4.sockets[0].getsockname()[1]
        with contextlib.suppress(OSError):
            servers.append(await asyncio.start_server(handle, "::1", port))
        url = f"{OAUTH_URL}?ext_app_id={app_id}&redirect_url=http://localhost:{port}{path}"
        prompt = LoginPrompt(url=url, port=port, path=path, _code=future)
        yield prompt
    finally:
        if not future.done():
            future.cancel()
        for server in servers:
            server.close()
        for server in servers:
            with contextlib.suppress(Exception):
                await server.wait_closed()
