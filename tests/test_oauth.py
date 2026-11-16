"""qobuz/oauth.py + QobuzClient.login_oauth: the link is always shown, the code
comes back through the loopback redirect server or a pasted address, and
nothing else is taken for one. Real sockets on 127.0.0.1; no Qobuz traffic
(the bundle fetch and the token exchange are stubbed)."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from qobit.qobuz import client as client_mod
from qobit.qobuz import oauth
from qobit.qobuz.client import QobuzClient, QobuzError


def run(coro):
    return asyncio.run(coro)


async def _get(port: int, path: str) -> httpx.Response:
    async with httpx.AsyncClient() as http:
        return await http.get(f"http://127.0.0.1:{port}{path}")


# --- LoginPrompt.submit: a pasted address ---


def _with_prompt(fn):
    async def go():
        async with oauth.redirect_server("123") as prompt:
            return fn(prompt), prompt

    return run(go())


def _addr(prompt, query, port=None):
    """The address the browser lands on for this login."""
    return f"http://localhost:{port or prompt.port}{prompt.path}?{query}"


def test_link_names_the_app_and_a_secret_loopback_redirect():
    _, prompt = _with_prompt(lambda p: None)
    url = urlparse(prompt.url)
    qs = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == oauth.OAUTH_URL
    assert qs["ext_app_id"] == ["123"]
    assert qs["redirect_url"] == [f"http://localhost:{prompt.port}{prompt.path}"]
    assert len(prompt.path) > 16 and "state" not in qs  # Qobuz drops state


def test_each_login_gets_its_own_secret_path():
    _, a = _with_prompt(lambda p: None)
    _, b = _with_prompt(lambda p: None)
    assert a.path != b.path


@pytest.mark.parametrize("key", ["code_autorisation", "code"])
def test_pasted_address_with_a_code_completes_the_login(key):
    async def go():
        async with oauth.redirect_server("123") as prompt:
            assert prompt.submit(f"  {_addr(prompt, f'{key}=abc123')}\n") == "ok"
            return await prompt.wait(timeout=1)

    assert run(go()) == "abc123"


def test_pasted_address_port_does_not_matter():
    """A pasted address is checked by its secret path; the port it names is
    whatever the browser showed (it never reached this machine)."""

    async def go():
        async with oauth.redirect_server("123") as prompt:
            assert prompt.submit(_addr(prompt, "code_autorisation=xyz", port=1)) == "ok"
            return await prompt.wait(timeout=1)

    assert run(go()) == "xyz"


@pytest.mark.parametrize(
    ("text", "outcome"),
    [
        ("", "no_code"),
        ("abc123", "no_code"),  # a bare code isn't an address
        ("{path}", "no_code"),
        ("{path}?code_autorisation=", "no_code"),
        ("{path}?code_autorisation=a%20b", "no_code"),  # whitespace
        ("http://localhost:1/?code_autorisation=abc", "wrong_login"),  # no secret
        ("http://localhost:1/other-login?code_autorisation=abc", "wrong_login"),
        ("{path}/?code_autorisation=abc", "wrong_login"),
    ],
)
def test_pasted_text_that_is_not_this_login_is_refused(text, outcome):
    def go(p):
        return p.submit(text.replace("{path}", f"http://localhost:1{p.path}"))

    result, prompt = _with_prompt(go)
    assert result == outcome
    assert not prompt.done or prompt._code.cancelled()


def test_a_second_code_is_ignored():
    async def go():
        async with oauth.redirect_server("123") as prompt:
            prompt.submit(_addr(prompt, "code=first"))
            assert prompt.submit(_addr(prompt, "code=second")) == "done"
            return await prompt.wait(timeout=1)

    assert run(go()) == "first"


def test_an_error_from_qobuz_fails_the_login_at_once():
    async def go():
        async with oauth.redirect_server("123") as prompt:
            assert prompt.submit(_addr(prompt, "error=access_denied")) == "error"
            await prompt.wait(timeout=5)

    with pytest.raises(oauth.LoginError, match="access_denied"):
        run(go())


def test_wait_times_out_with_a_login_error():
    async def go():
        async with oauth.redirect_server("123") as prompt:
            await prompt.wait(timeout=0.05)

    with pytest.raises(oauth.LoginError, match="timed out"):
        run(go())


# --- the loopback redirect server ---


def test_redirect_server_takes_the_code_from_the_browser():
    async def go():
        async with oauth.redirect_server("123") as prompt:
            r = await _get(prompt.port, f"{prompt.path}?code_autorisation=c0de")
            return r, await prompt.wait(timeout=1)

    r, code = run(go())
    assert r.status_code == 200 and "Logged in" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert code == "c0de"


def test_redirect_server_ignores_other_paths_and_methods():
    """favicon.ico, the bare root, a POST, the secret path without a code:
    none of them is a login."""

    async def go():
        async with oauth.redirect_server("123") as prompt:
            fav = await _get(prompt.port, "/favicon.ico?code=x")
            root = await _get(prompt.port, "/?code=x")
            async with httpx.AsyncClient() as http:
                post = await http.post(f"http://127.0.0.1:{prompt.port}{prompt.path}?code=x")
            empty = await _get(prompt.port, prompt.path)
            return fav, root, post, empty, prompt.done

    fav, root, post, empty, done = run(go())
    codes = (fav.status_code, root.status_code, post.status_code, empty.status_code)
    assert codes == (404, 404, 405, 400)
    assert not done


def test_redirect_server_refuses_a_code_without_the_secret_path():
    """The login CSRF: a page or process that found the port hands qobit a
    code for its own account. Without the secret path it is refused."""

    async def go():
        async with oauth.redirect_server("123") as prompt:
            r = await _get(prompt.port, "/?code_autorisation=evil")
            return r, prompt.done

    r, done = run(go())
    assert r.status_code == 404 and not done  # same as any unknown page


def test_redirect_server_listens_on_loopback_only(monkeypatch):
    """127.0.0.1 on an OS-picked port (bound once: no probe-then-bind race),
    then ::1 on the same port; never a wildcard or LAN address."""
    binds = []
    real = asyncio.start_server

    async def spy(handler, host, port):
        binds.append((host, port))
        return await real(handler, host, port)

    monkeypatch.setattr(oauth.asyncio, "start_server", spy)

    async def go():
        async with oauth.redirect_server("123") as prompt:
            return prompt.port

    port = run(go())
    assert binds[0] == ("127.0.0.1", 0)
    assert all(host in ("127.0.0.1", "::1") for host, _ in binds)
    assert all(p == port for _, p in binds[1:])


def test_the_server_is_gone_after_the_login():
    async def go():
        async with oauth.redirect_server("123") as prompt:
            port = prompt.port
        return port  # noqa: RET504 — read after the server closed

    port = run(go())
    with pytest.raises(httpx.ConnectError):
        run(_get(port, "/?code=late"))


# --- QobuzClient.login_oauth end to end (Qobuz stubbed) ---


class _Resp:
    def __init__(self, data):
        self._data = data
        self.is_error = False
        self.status_code = 200

    def json(self):
        return self._data


def _stub_qobuz(monkeypatch, client, opened, *, display=False):
    async def no_bundle():
        client._private_key = "pk"
        client._prod_app_id = "123"
        return ["123"]

    sent = []

    async def fake_send(method, url, **kw):
        sent.append((method, url, kw))
        if url.endswith("oauth/callback"):
            return _Resp({"token": "interim"})
        return _Resp({"user_auth_token": "final-token"})

    monkeypatch.setattr(client, "_fetch_bundle_credentials", no_bundle)
    monkeypatch.setattr(client, "_send", fake_send)
    monkeypatch.setattr(client_mod.webbrowser, "open", opened.append)
    monkeypatch.setattr(client_mod, "_has_graphical_browser", lambda: display)
    return sent


@pytest.mark.parametrize("display", [True, False])
def test_login_oauth_shows_the_link_then_finishes_from_a_paste(monkeypatch, display):
    """The link is handed out first, whatever happens to the browser; a
    pasted address completes the login with no local redirect at all."""
    client = QobuzClient()
    opened, shown = [], []
    sent = _stub_qobuz(monkeypatch, client, opened, display=display)

    def show(prompt):
        shown.append(prompt.url)
        assert prompt.submit(_addr(prompt, "code_autorisation=pasted")) == "ok"

    run(client.login_oauth(show_url=show))
    assert len(shown) == 1 and shown[0].startswith(oauth.OAUTH_URL)
    # without a display, webbrowser would start lynx in the terminal: don't
    assert opened == (shown if display else [])
    assert sent[0][2]["params"] == {"code": "pasted", "private_key": "pk"}
    assert client.user_auth_token == "final-token" and client._app_id == "123"


def test_login_oauth_finishes_from_the_browser_redirect(monkeypatch):
    """The redirect completes the login, and the browser still gets its
    "Logged in" page while the server shuts down."""
    client = QobuzClient()
    _stub_qobuz(monkeypatch, client, [])
    browser = []

    def show(prompt):
        path = f"{prompt.path}?code_autorisation=redir"
        browser.append(asyncio.get_running_loop().create_task(_get(prompt.port, path)))

    async def go():
        await client.login_oauth(show_url=show)
        return await browser[0]

    r = run(go())
    assert client.user_auth_token == "final-token"
    assert r.status_code == 200 and "Logged in" in r.text


def test_login_oauth_prints_the_link_by_default(monkeypatch, capsys):
    client = QobuzClient()
    _stub_qobuz(monkeypatch, client, [])
    monkeypatch.setattr(oauth, "LOGIN_TIMEOUT", 0.05)
    with pytest.raises(QobuzError, match="timed out"):
        run(client.login_oauth())
    assert "https://www.qobuz.com/signin/oauth?ext_app_id=123" in capsys.readouterr().out


def test_login_oauth_survives_a_browser_that_fails_to_open(monkeypatch):
    client = QobuzClient()
    _stub_qobuz(monkeypatch, client, [], display=True)

    def broken(url):
        raise RuntimeError("no browser")

    monkeypatch.setattr(client_mod.webbrowser, "open", broken)
    run(client.login_oauth(show_url=lambda p: p.submit(_addr(p, "code=ok"))))
    assert client.user_auth_token == "final-token"


def test_login_oauth_turns_a_login_error_into_a_qobuz_error(monkeypatch):
    client = QobuzClient()
    _stub_qobuz(monkeypatch, client, [])
    with pytest.raises(QobuzError, match="refused"):
        run(client.login_oauth(show_url=lambda p: p.submit(_addr(p, "error=denied"))))


def test_login_oauth_explains_a_rejected_code(monkeypatch):
    client = QobuzClient()
    _stub_qobuz(monkeypatch, client, [])

    async def reject(method, url, **kw):
        r = _Resp({"message": "No result matching given argument"})
        r.is_error, r.status_code, r.text = True, 404, ""
        return r

    monkeypatch.setattr(client, "_send", reject)
    with pytest.raises(QobuzError, match="didn't accept the login code"):
        run(client.login_oauth(show_url=lambda p: p.submit(_addr(p, "code=old"))))
