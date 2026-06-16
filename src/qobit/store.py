import json
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
    _path(name).write_text(json.dumps(data, indent=2))
