"""Open YouTube in the default browser."""

from __future__ import annotations

import subprocess
import sys
import webbrowser


def open_youtube(*, url: str = "https://www.youtube.com/") -> None:
    u = (url or "").strip() or "https://www.youtube.com/"
    if sys.platform == "darwin":
        subprocess.run(["open", u], check=False)
        return
    webbrowser.open(u, new=2)

