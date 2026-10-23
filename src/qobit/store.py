import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path.home() / ".local" / "share" / "qobit"


def _path(name: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / name


def load(name: str) -> dict[str, Any]:
    p = _path(name)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def save(name: str, data: dict[str, Any]) -> None:
    """Write atomically (tmp file + rename) so a crash/kill mid-write can't
    leave a truncated, unparseable file behind."""
    path = _path(name)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)
