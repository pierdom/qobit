"""Owner-only files and directories for qobit's own state.

config.json holds the Qobuz session token; DATA_DIR holds the mpv IPC socket
(mpv's `run` command executes programs) and the listening history. None of it
should depend on the user's umask, so qobit's directories are 0700 and its
files are created 0600 from the first byte (never written, then chmod-ed).
"""

import contextlib
import os
import tempfile
from pathlib import Path


def private_dir(path: Path) -> Path:
    """Create ``path`` (and missing parents) and make the leaf 0700. Parents
    are shared XDG directories (~/.config, ~/.local/share): left alone."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)  # tighten a directory an older qobit created 0755
    return path


def write_private(path: Path, text: str) -> None:
    """Atomically replace ``path`` with ``text``, mode 0600. The temp file is
    created 0600 by mkstemp, so the content is never readable by others, not
    even between the write and the rename, and a crash leaves no readable
    copy behind under a predictable name."""
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
