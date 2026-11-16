"""Where the Qobuz session token lives: the system keyring when there is one.

The token is the one real credential (whoever holds it uses the account), so
it goes to the macOS Keychain or a Linux Secret Service (GNOME Keyring,
KWallet) through ``keyring``. config.json then holds only a marker
(``"token_store": "keyring"``) next to the non-secret parts of the session.
Without a usable keyring (headless Linux, SSH without a desktop session) the
token stays in config.json (0600), as before; ``QOBIT_KEYRING=0`` forces
that. config.py is the only caller.

Two hazards handled here:
- Probing Linux backends makes GLib print warnings straight to fd 2, which
  would garble the TUI: every keyring call runs with fd 2 silenced.
- Without a D-Bus session there is no Secret Service: skip probing at all.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from typing import Any

SERVICE = "qobit"
ACCOUNT = "qobuz-session-token"


def _keyring() -> Any:
    """The keyring module (tests replace this with an in-memory fake: they
    must never touch the real Keychain)."""
    import keyring

    return keyring


def enabled() -> bool:
    if os.environ.get("QOBIT_KEYRING", "").strip().lower() in ("0", "no", "off", "false"):
        return False
    return not (sys.platform.startswith("linux") and not os.environ.get("DBUS_SESSION_BUS_ADDRESS"))


@contextlib.contextmanager
def _quiet_stderr() -> Iterator[None]:
    """Silence fd 2 (C libraries write there directly, not via sys.stderr)."""
    try:
        saved = os.dup(2)
    except OSError:
        yield
        return
    try:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), 2)
            yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


def name() -> str:
    """A readable name for the keyring in use, for `qobit auth status`."""
    if sys.platform == "darwin":
        return "the macOS Keychain"
    try:
        with _quiet_stderr():
            backend = _keyring().get_keyring()
        label = getattr(backend, "name", "") or type(backend).__name__
    except Exception:
        label = ""
    return f"the system keyring ({label})" if label else "the system keyring"


def store(token: str) -> bool:
    """Put the token in the keyring. False when there's none usable: the
    caller keeps it in config.json."""
    if not enabled():
        return False
    try:
        with _quiet_stderr():
            kr = _keyring()
            kr.set_password(SERVICE, ACCOUNT, token)
            # A backend that accepts writes but can't read them back (a
            # locked or broken store) is no better than none.
            return kr.get_password(SERVICE, ACCOUNT) == token
    except Exception:
        return False


def load() -> str | None:
    """The token from the keyring, or None (absent, or no keyring here)."""
    if not enabled():
        return None
    try:
        with _quiet_stderr():
            return _keyring().get_password(SERVICE, ACCOUNT)
    except Exception:
        return None


def delete() -> None:
    if not enabled():
        return
    with contextlib.suppress(Exception), _quiet_stderr():
        _keyring().delete_password(SERVICE, ACCOUNT)
