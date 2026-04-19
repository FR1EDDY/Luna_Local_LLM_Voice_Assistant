"""Cursor IDE: open app, focus AI Chat, dictation (best-effort), clipboard → speech text."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
import time

from jarvis import config


def _darwin_only() -> bool:
    return sys.platform == "darwin"


def _osascript(source: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["osascript", "-e", source],
        check=False,
        capture_output=True,
        text=True,
    )
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    return r.returncode, out, err


def _dictation_key_from_symbolic_hotkeys() -> tuple[int | None, int]:
    """
    Read the user's dictation shortcut from ``com.apple.symbolichotkeys`` (entry 164).
    Returns (key_code, tap_count). tap_count is 2 for Fn (classic double-press), else 1.
    Missing or invalid → (None, 0).
    """
    path = os.path.expanduser("~/Library/Preferences/com.apple.symbolichotkeys.plist")
    try:
        with open(path, "rb") as f:
            pl = plistlib.load(f)
    except OSError:
        return None, 0
    ahk = pl.get("AppleSymbolicHotKeys") or {}
    entry = ahk.get("164") or ahk.get(164)
    if not isinstance(entry, dict) or not entry.get("enabled"):
        return None, 0
    val = entry.get("value") or {}
    params = val.get("parameters") or []
    if len(params) < 2:
        return None, 0
    kc = params[1]
    if not isinstance(kc, int) or kc < 0 or kc > 127:
        return None, 0
    if kc in (65535, 0xFFFF):
        return None, 0
    taps = 2 if kc == 63 else 1
    return kc, taps


def _send_dictation_keycodes_applescript(key_code: int, tap_count: int, pause_s: float = 0.22) -> bool:
    """Simulate dictation shortcut via System Events (needs Accessibility for the caller)."""
    if not _darwin_only() or key_code < 0 or tap_count < 1:
        return False
    pause_s = max(0.05, min(0.6, pause_s))
    inner = ""
    for i in range(tap_count):
        if i:
            inner += f"\n        delay {pause_s:.2f}\n        "
        inner += f"key code {key_code}"
    script = f"""
    tell application "System Events"
        {inner}
    end tell
    """
    code, _out, _err = _osascript(script)
    return code == 0


def _send_dictation_keycodes_quartz(key_code: int, tap_count: int, pause_s: float = 0.28) -> bool:
    """
    Post full key-down/key-up pairs to ``kCGHIDEventTap`` (usually works when AppleScript ``key code`` does not).
    """
    if not _darwin_only() or key_code < 0 or tap_count < 1:
        return False
    pause_s = max(0.08, min(0.7, pause_s))
    try:
        from Quartz import CGEventCreateKeyboardEvent, CGEventPost, kCGHIDEventTap
    except ImportError:
        return False
    for i in range(tap_count):
        if i:
            time.sleep(pause_s)
        down = CGEventCreateKeyboardEvent(None, key_code, True)
        up = CGEventCreateKeyboardEvent(None, key_code, False)
        if down is None or up is None:
            return False
        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)
    return True


def _run_dictation_key_sequence(key_code: int, tap_count: int) -> bool:
    """Try Quartz first (HID tap), then AppleScript."""
    if _send_dictation_keycodes_quartz(key_code, tap_count):
        return True
    return _send_dictation_keycodes_applescript(key_code, tap_count)


def try_start_dictation_system_shortcut() -> bool:
    """
    Trigger dictation the same way as the hardware shortcut: optional env override,
    then read plist when possible, otherwise double-press Fn (key code 63).
    """
    if not _darwin_only():
        return False
    if config.CURSOR_DICTATION_KEYCODE is not None:
        taps = config.CURSOR_DICTATION_TAPS
        if taps is None:
            taps = 2 if config.CURSOR_DICTATION_KEYCODE == 63 else 1
        if _run_dictation_key_sequence(config.CURSOR_DICTATION_KEYCODE, taps):
            return True
    kc, taps = _dictation_key_from_symbolic_hotkeys()
    if kc is not None and taps >= 1:
        if _run_dictation_key_sequence(kc, taps):
            return True
    return _run_dictation_key_sequence(63, 2)


def try_start_dictation_combined() -> bool:
    """Menu click (if present) then system shortcut simulation."""
    if try_start_dictation_front_app():
        return True
    return try_start_dictation_system_shortcut()


def fire_macos_dictation_handoff(*, settle_s: float = 0.35) -> None:
    """
    Run after Luna releases the mic so Cursor is frontmost and TTS has finished.
    Safe to call from a background thread.
    """
    if not _darwin_only():
        return
    time.sleep(max(0.0, settle_s))
    try_start_dictation_combined()


def open_cursor() -> bool:
    if not _darwin_only():
        return False
    r = subprocess.run(
        ["open", "-a", config.CURSOR_APP_NAME],
        check=False,
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def focus_cursor_ai_chat() -> bool:
    """
    Send the default Cursor shortcut to open/focus the AI Chat pane (usually Cmd+L).
    Override via ``jarvis.config.CURSOR_CHAT_KEYSTROKE`` (see config).
    """
    if not _darwin_only():
        return False
    app = config.CURSOR_APP_NAME.replace("\\", "\\\\").replace('"', '\\"')
    ks = re.sub(r"\s+", "", config.CURSOR_CHAT_KEYSTROKE.strip().lower())
    # AppleScript keystroke: "l" with command, "i" with command for Composer, etc.
    if ks in ("cmd+l", "command+l", "⌘l"):
        key = "l"
        mods = "command down"
    elif ks in ("cmd+i", "command+i", "⌘i"):
        key = "i"
        mods = "command down"
    elif ks in ("cmd+shift+l", "command+shift+l"):
        key = "l"
        mods = "command down, shift down"
    else:
        key = "l"
        mods = "command down"
    script = f'''
    tell application "{app}"
        activate
    end tell
    delay 1.2
    tell application "System Events"
        tell process "{app}"
            set frontmost to true
        end tell
        keystroke "{key}" using {{{mods}}}
    end tell
    '''
    code, _out, _err = _osascript(script)
    return code == 0


def try_start_dictation_front_app() -> bool:
    """
    Best-effort: Edit → Start Dictation in the frontmost app.
    Electron apps (e.g. Cursor) often omit this menu item; use
    ``try_start_dictation_system_shortcut`` as fallback.
    Requires Accessibility permission for the app running this script.
    """
    if not _darwin_only():
        return False
    script = r'''
    tell application "System Events"
        tell (first process whose frontmost is true)
            try
                click menu item "Start Dictation" of menu 1 of menu bar item "Edit" of menu bar 1
                return "ok"
            on error
                try
                    click menu item "Start Dictation" of menu "Edit" of menu bar 1
                    return "ok"
                on error
                    error "no Start Dictation menu"
                end try
            end try
        end tell
    end tell
    '''
    code, out, _err = _osascript(script)
    return code == 0 and out == "ok"


def activate_cursor_mode(
    *,
    delay_before_dictation_s: float = 0.5,
    with_dictation: bool = True,
) -> tuple[bool, str]:
    """
    Open Cursor and focus AI chat. Optionally start dictation immediately (legacy path).
    When ``with_dictation`` is False, Luna should release the mic and call
    ``fire_macos_dictation_handoff`` after TTS so dictation is not fighting Vosk.

    Returns (overall_ok, short_user_message).
    """
    if not _darwin_only():
        return False, "Cursor mode only works on macOS."

    if not open_cursor():
        return False, "I couldn't open Cursor."

    if not focus_cursor_ai_chat():
        return True, "Cursor is open. Press Command L for AI chat, then use dictation or type your prompt."

    if not with_dictation:
        return True, (
            "Cursor AI chat is ready. Releasing the microphone for macOS dictation. "
            "When you're done in Cursor, start a new listening session with a double clap, "
            "the same way you started this one—after I finish talking."
        )

    time.sleep(max(0.0, delay_before_dictation_s))
    dictation_ok = try_start_dictation_combined()
    if dictation_ok:
        return True, (
            "Cursor AI chat is focused and I triggered macOS dictation (menu or your dictation shortcut). "
            "When the AI answers, copy its message, then say read cursor reply for the short version, "
            "or read full cursor reply for all prose without code."
        )
    return True, (
        "Cursor AI chat is focused, but I could not start dictation automatically. "
        "Allow Accessibility for the app running Jarvis, and in System Settings → Keyboard → Dictation "
        "set a shortcut AppleScript can send (for example press Fn key twice). "
        "You can also press your dictation shortcut manually. "
        "When the AI answers, copy its message, then say read cursor reply or read full cursor reply."
    )


def _pbpaste() -> str:
    r = subprocess.run(["pbpaste"], check=False, capture_output=True, text=True)
    return (r.stdout or "").strip()


def strip_markdown_code_for_speech(text: str) -> str:
    """Remove fenced code blocks and inline `` `code` `` chunks; keep prose."""
    s = text or ""
    s = re.sub(r"```[\w\-+#]*\s*\n[\s\S]*?```", " ", s)
    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"`[^`\n]+`", " ", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def prose_conclusion(text: str, *, max_chars: int = 1200) -> str:
    """
    For long replies: prefer the last one or two non-empty paragraphs (often the wrap-up).
    """
    body = strip_markdown_code_for_speech(text)
    if not body:
        return ""
    if len(body) <= max_chars:
        return body
    paras = [p.strip() for p in re.split(r"\n\s*\n+", body) if p.strip()]
    if not paras:
        return body[:max_chars]
    if len(paras) == 1:
        return paras[-1][:max_chars]
    tail = "\n\n".join(paras[-2:])
    return tail[:max_chars] if len(tail) > max_chars else tail


def clipboard_text_for_cursor_reply(*, brief: bool) -> tuple[bool, str]:
    """
    Read pasteboard; return (ok, text_to_speak).
    ``brief`` uses last paragraphs when content is long.
    """
    max_chars = 5000
    raw = _pbpaste()
    if not raw:
        return False, "Your clipboard is empty. Copy the AI message in Cursor first."
    if brief:
        spoken = prose_conclusion(raw)
    else:
        spoken = strip_markdown_code_for_speech(raw)
    if not spoken:
        return False, "After removing code, there was nothing left to read."
    if len(spoken) > max_chars:
        spoken = spoken[: max_chars - 12].rstrip() + " … truncated."
    return True, spoken
