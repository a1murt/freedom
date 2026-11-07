"""Locating and running the ffmpeg/ffprobe binaries.

Resolution order: ``FFMPEG_BIN`` / ``FFPROBE_BIN`` environment variables, then ``PATH``,
then the usual Homebrew / system locations. Every runner here degrades to ``None`` instead
of raising, so feature extraction can fall back gracefully when a tool is missing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_FALLBACK_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")


def find_binary(name: str, env_var: str) -> str:
    override = os.environ.get(env_var)
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    for directory in _FALLBACK_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file():
            return str(candidate)
    return name  # let subprocess report the failure; callers degrade to None


FFMPEG = find_binary("ffmpeg", "FFMPEG_BIN")
FFPROBE = find_binary("ffprobe", "FFPROBE_BIN")


def run_tool(args: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    """Run a command and return the completed process, or ``None`` if it could not run."""
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired:
        log.warning("%s timed out after %ss", args[0], timeout)
    except OSError as exc:
        log.warning("cannot run %s: %s", args[0], exc)
    return None
