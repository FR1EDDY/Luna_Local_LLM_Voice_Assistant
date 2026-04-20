"""
Hot-reload ~/.jarvis.env without restarting.

Polls every 5 s for mtime changes; re-applies changed keys to os.environ
and updates the runtime-safe config attributes immediately.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

_WATCHED_PATH = Path.home() / ".jarvis.env"

_RUNTIME_SAFE_KEYS: dict[str, str] = {
    "JARVIS_LUNA_MIC_GAIN": "LUNA_MIC_GAIN",
    "JARVIS_LUNA_CMD_VOICE_RMS": "LUNA_CMD_VOICE_RMS",
    "JARVIS_LUNA_CMD_END_SILENCE_S": "LUNA_CMD_END_SILENCE_S",
    "JARVIS_LUNA_POST_TTS_GAP_S": "LUNA_POST_TTS_GAP_S",
    "JARVIS_LUNA_POST_WAKE_GAP_S": "LUNA_POST_WAKE_GAP_S",
    "JARVIS_WAKE_WORD_COOLDOWN_S": "WAKE_WORD_COOLDOWN_S",
    "JARVIS_LUNA_CHAT_TIMEOUT_S": "LUNA_CHAT_TIMEOUT_S",
    "JARVIS_LUNA_TTS_SPEED": "LUNA_TTS_SPEED",
}


def _load_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                result[k] = v
    except Exception:
        pass
    return result


def _apply_changes(changed: dict[str, str]) -> None:
    from jarvis import config

    for env_key, val in changed.items():
        os.environ[env_key] = val
        cfg_attr = _RUNTIME_SAFE_KEYS.get(env_key)
        if cfg_attr is None:
            continue
        try:
            if cfg_attr == "LUNA_MIC_GAIN":
                setattr(config, cfg_attr, config.clamp_luna_mic_gain(float(val)))
            elif cfg_attr == "LUNA_TTS_SPEED":
                setattr(config, cfg_attr, val.strip().lower())
            else:
                setattr(config, cfg_attr, float(val))
        except (ValueError, AttributeError):
            pass

    keys = list(changed.keys())
    print(f"[Config] Hot-reloaded {_WATCHED_PATH.name}: {keys}", flush=True)


def start_env_watcher() -> None:
    """Start the background polling thread. Safe to call if file doesn't exist yet."""

    def _watch() -> None:
        last_mtime = 0.0
        last_vars: dict[str, str] = {}
        while True:
            time.sleep(5)
            try:
                if not _WATCHED_PATH.is_file():
                    continue
                mtime = _WATCHED_PATH.stat().st_mtime
                if mtime == last_mtime:
                    continue
                last_mtime = mtime
                new_vars = _load_env_file(_WATCHED_PATH)
                changed = {k: v for k, v in new_vars.items() if last_vars.get(k) != v}
                if changed:
                    _apply_changes(changed)
                last_vars = new_vars
            except Exception:
                pass

    t = threading.Thread(target=_watch, daemon=True, name="jarvis-env-watcher")
    t.start()
