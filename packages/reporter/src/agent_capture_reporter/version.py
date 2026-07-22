"""Single source of the reporter version, stamped into every manifest."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-capture-reporter")
except PackageNotFoundError:  # not installed (e.g. running from a bare checkout)
    __version__ = "0.1.0"
