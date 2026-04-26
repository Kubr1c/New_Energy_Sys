from __future__ import annotations

from pathlib import Path

import requests


def ensure_dir(path: Path) -> Path:
    """Create a directory if needed and return the same path for chaining."""

    path.mkdir(parents=True, exist_ok=True)
    return path


def download_file(url: str, target: Path, timeout: int = 60) -> Path:
    """Download a remote file with streaming and atomic replacement.

    A temporary suffix prevents half-written files from being mistaken as valid
    cached data after an interrupted download.
    """

    ensure_dir(target.parent)
    temp_target = target.with_suffix(target.suffix + ".tmp")

    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with temp_target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)

    temp_target.replace(target)
    return target
