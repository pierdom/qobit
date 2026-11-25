"""Secret: a str whose repr is masked.

Textual's crash screen prints every frame's local variables (Rich traceback,
``show_locals=True``), and crash screens get pasted into bug reports. A
credential held as ``Secret`` shows as ``'<redacted>'`` there, and in any
other repr (a dict of headers included), while it still works as the string
it is: f-strings, ``str()``, JSON, httpx headers and the keyring all get the
real value. Wrap credentials where they enter qobit: the session token
(config, keyring, login), the Connect JWT, the OAuth codes.
"""

from typing import overload


class Secret(str):
    __slots__ = ()

    def __repr__(self) -> str:
        return "'<redacted>'"


@overload
def secret(value: str) -> str: ...
@overload
def secret(value: None) -> None: ...
@overload
def secret(value: str | None) -> str | None: ...
def secret(value: str | None) -> str | None:
    """``value`` as a Secret (None and "" pass through)."""
    return Secret(value) if value else value
