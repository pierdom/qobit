"""Every panel that colours its border title colours its subtitle too.

Unset, a border subtitle takes the border's colour: `$accent 40%` on an
unfocused panel, which made the artist panels' "t or click the title to
switch" hint barely readable."""

from __future__ import annotations

import re
from pathlib import Path

import qobit.ui

# Rules whose subtitle colours itself (the transport's signal readout) or
# that never set one (the login dialog).
_EXEMPT = {"TransportBar.-playing", "LoginScreen #login"}

_RULE = re.compile(r"\n\s*([^\n{}]+?)\s*\{([^{}]*)\}")


def test_border_subtitles_have_a_readable_colour():
    missing = []
    for path in Path(qobit.ui.__file__).parent.rglob("*.py"):
        for selector, body in _RULE.findall(path.read_text()):
            if selector in _EXEMPT or "border-title-color" not in body:
                continue
            if "border-subtitle-color" not in body:
                missing.append(f"{path.name}: {selector}")
    assert missing == []
