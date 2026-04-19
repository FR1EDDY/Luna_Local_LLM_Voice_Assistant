"""Small helpers for launching macOS apps (via `open -a`)."""

from __future__ import annotations

import subprocess
import sys


def open_app(app_name: str) -> bool:
    """
    Best-effort launch/activate an application by display name.

    Returns True if the `open` command succeeded, else False.
    """
    if sys.platform != "darwin":
        return False
    name = (app_name or "").strip()
    if not name:
        return False
    try:
        r = subprocess.run(
            ["open", "-a", name],
            check=False,
            capture_output=True,
            text=True,
        )
        return r.returncode == 0
    except Exception:
        return False

