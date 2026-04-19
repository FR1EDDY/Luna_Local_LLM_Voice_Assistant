"""Launch or focus Notion (and optional fullscreen) on macOS."""

from __future__ import annotations

import subprocess
import sys
import time


def open_notion(*, app_name: str = "Notion", try_fullscreen: bool = False) -> None:
    """Launch or switch to Notion after Spotify (macOS ``open -a``)."""
    if sys.platform != "darwin":
        return
    r = subprocess.run(
        ["open", "-a", app_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if err:
            print(f"open -a {app_name!r} failed: {err}", file=sys.stderr)
        return
    if not try_fullscreen:
        return
    time.sleep(0.55)
    safe_proc = app_name.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "System Events"
        tell process "{safe_proc}"
            set frontmost to true
            keystroke "f" using {{command down, control down}}
        end tell
    end tell
    '''
    fs = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if fs.returncode != 0 and (fs.stderr or fs.stdout):
        print(
            (fs.stderr or fs.stdout or "").strip()
            or "Notion fullscreen keystroke failed (grant Accessibility to Terminal/Cursor if needed).",
            file=sys.stderr,
        )
