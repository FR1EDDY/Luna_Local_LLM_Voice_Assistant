"""Spotify: in-app volume (AppleScript) and play URI/URL."""

from __future__ import annotations

import subprocess
import sys
from urllib.parse import urlparse


def _osascript_line(source: str) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["osascript", "-e", source],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        print("AppleScript timed out.", file=sys.stderr)
        return 1, ""
    return r.returncode, (r.stdout or "").strip()


def get_sound_volume() -> int | None:
    """Spotify in-app volume 0–100, or None if AppleScript fails."""
    if sys.platform != "darwin":
        return None
    code, out = _osascript_line('tell application "Spotify" to return sound volume')
    if code != 0 or not out:
        return None
    try:
        return int(round(float(out)))
    except ValueError:
        return None


def set_sound_volume(volume: int) -> bool:
    if sys.platform != "darwin":
        return False
    v = max(0, min(100, int(volume)))
    code, _ = _osascript_line(f'tell application "Spotify" to set sound volume to {v}')
    return code == 0


def play_pause() -> bool:
    """Toggle play/pause. Returns True if AppleScript ran successfully."""
    if sys.platform != "darwin":
        return False
    code, _ = _osascript_line('tell application "Spotify" to playpause')
    return code == 0


def play() -> bool:
    if sys.platform != "darwin":
        return False
    code, _ = _osascript_line('tell application "Spotify" to play')
    return code == 0


def pause() -> bool:
    if sys.platform != "darwin":
        return False
    code, _ = _osascript_line('tell application "Spotify" to pause')
    return code == 0


def next_track() -> bool:
    if sys.platform != "darwin":
        return False
    code, _ = _osascript_line('tell application "Spotify" to next track')
    return code == 0


def previous_track() -> bool:
    if sys.platform != "darwin":
        return False
    code, _ = _osascript_line('tell application "Spotify" to previous track')
    return code == 0


def get_current_track() -> str | None:
    """Returns 'Artist - Track name' or None if Spotify is not playing / unavailable."""
    if sys.platform != "darwin":
        return None
    code, out = _osascript_line(
        'tell application "Spotify" to return (artist of current track) & " - " & (name of current track)'
    )
    if code != 0 or not out or out.strip() == " - ":
        return None
    return out.strip() or None


def normalize_spotify_uri(value: str) -> str:
    """Accept spotify: URI or open.spotify.com link → spotify:<type>:<id>."""
    raw = (value or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("Empty Spotify URI/URL.")

    if raw.startswith("spotify:"):
        return raw.split("?", 1)[0].rstrip("/")

    u = urlparse(raw)
    host = (u.netloc or "").lower()
    if host.endswith("open.spotify.com"):
        parts = [p for p in (u.path or "").split("/") if p]
        if len(parts) >= 2:
            kind, item_id = parts[0], parts[1]
            if kind in {"track", "playlist", "album", "artist"} and item_id:
                return f"spotify:{kind}:{item_id}"

    raise ValueError(f"Unsupported Spotify link/URI: {value!r}")


def play_spotify_uri(uri_or_url: str, *, activate: bool = False) -> None:
    try:
        uri = normalize_spotify_uri(uri_or_url)
    except ValueError as e:
        print(f"Spotify: {e}", file=sys.stderr)
        return
    safe = uri.replace("\\", "\\\\").replace('"', '\\"')
    act = "        activate\n" if activate else ""
    script = f'''tell application "Spotify"
{act}        play track "{safe}"
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        print("Spotify AppleScript timed out.", file=sys.stderr)
        return
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        print(err or "Spotify AppleScript failed", file=sys.stderr)
