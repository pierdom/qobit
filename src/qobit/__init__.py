"""qobit — bit-perfect Qobuz client."""

from importlib.metadata import PackageNotFoundError, version

# Single source of truth is pyproject.toml's [project].version — bump it there
# (and add a CHANGELOG.md entry); this reads it from the installed metadata.
try:
    __version__ = version("qobit")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0+unknown"
