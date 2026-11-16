import json
from pathlib import Path
from typing import Any

from ._fs import private_dir, write_private
from .qobuz.sanitize import clean_json

DATA_DIR = Path.home() / ".local" / "share" / "qobit"


def _path(name: str) -> Path:
    # 0700: holds the mpv IPC socket (mpv's `run` command executes programs)
    # and the listening history.
    return private_dir(DATA_DIR) / name


def load(name: str) -> dict[str, Any]:
    p = _path(name)
    if p.exists():
        try:
            # Cleaned on load too: files saved before Qobuz text was cleaned
            # (qobuz/sanitize.py) may still carry escape sequences.
            return clean_json(json.loads(p.read_text()))
        except Exception:
            return {}
    return {}


def save(name: str, data: dict[str, Any]) -> None:
    """Write atomically (temp file + rename, 0600) so a crash/kill mid-write
    can't leave a truncated, unparseable file behind."""
    write_private(_path(name), json.dumps(data, indent=2))
