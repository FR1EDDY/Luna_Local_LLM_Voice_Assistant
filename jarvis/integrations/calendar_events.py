"""Create/delete Calendar.app events (macOS) for local voice commands."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class ParsedWhen:
    start: datetime
    end: datetime


_DATE_TOKEN_RE = re.compile(r"^(today|tomorrow|\d{4}-\d{2}-\d{2})$", re.IGNORECASE)
_TIME_TOKEN_RE = re.compile(
    r"^(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?$",
    re.IGNORECASE,
)
_TIME_HH_MM_SPACE_RE = re.compile(r"^(?P<h>\d{1,2})\s+(?P<m>\d{1,2})\s*(?P<ampm>am|pm)?$", re.IGNORECASE)

_NUM_WORDS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
}


def _words_to_int(s: str) -> int | None:
    """
    Small spoken-number parser for 0–59. Handles e.g. "fifteen", "twenty one".
    """
    raw = (s or "").strip().lower().replace("-", " ")
    if not raw:
        return None
    parts = [p for p in raw.split() if p]
    if not parts:
        return None
    if len(parts) == 1:
        return _NUM_WORDS.get(parts[0])
    if len(parts) == 2 and parts[0] in _NUM_WORDS and parts[1] in _NUM_WORDS:
        a = _NUM_WORDS[parts[0]]
        b = _NUM_WORDS[parts[1]]
        if a in (20, 30, 40, 50) and 0 <= b <= 9:
            return a + b
    return None


def _normalize_time_token(token: str) -> str:
    """
    Normalize common Vosk transcriptions like:
    - "three fifteen" -> "3 15"
    - "fifteen thirteen" -> "15 13"
    Preserves any trailing am/pm.
    """
    t = (token or "").strip().lower().replace(".", "").replace("-", " ")
    if not t:
        return ""
    parts = [p for p in t.split() if p]
    if not parts:
        return ""
    ampm = ""
    if parts and parts[-1] in ("am", "pm"):
        ampm = parts[-1]
        parts = parts[:-1]
    if len(parts) == 1:
        h = _words_to_int(parts[0])
        if h is not None:
            base = f"{h}"
            return f"{base} {ampm}".strip()
    if len(parts) == 2:
        h = _words_to_int(parts[0])
        m = _words_to_int(parts[1])
        if h is not None and m is not None:
            base = f"{h} {m}"
            return f"{base} {ampm}".strip()
        # e.g. "four pm" (ampm is stripped earlier)
        if h is not None and m is None:
            base = f"{h}"
            return f"{base} {ampm}".strip()
    return f"{' '.join(parts)} {ampm}".strip()


def _parse_date_token(token: str, *, now: datetime | None = None) -> date | None:
    t = (token or "").strip().lower()
    if not _DATE_TOKEN_RE.match(t):
        return None
    if now is None:
        now = datetime.now()
    if t == "today":
        return now.date()
    if t == "tomorrow":
        return (now + timedelta(days=1)).date()
    try:
        return date.fromisoformat(t)
    except ValueError:
        return None


def parse_date_token(token: str, *, now: datetime | None = None) -> date | None:
    """Public wrapper (used by local command layer)."""
    return _parse_date_token(token, now=now)


def _parse_time_token(token: str) -> time | None:
    t = _normalize_time_token(token)
    m = _TIME_TOKEN_RE.match(t)
    if m:
        h = int(m.group("h"))
        mi = int(m.group("m") or "0")
        ampm = (m.group("ampm") or "").lower()
    else:
        m2 = _TIME_HH_MM_SPACE_RE.match(t)
        if not m2:
            return None
        h = int(m2.group("h"))
        mi = int(m2.group("m") or "0")
        ampm = (m2.group("ampm") or "").lower()

    if mi < 0 or mi > 59:
        return None
    if ampm:
        if h < 1 or h > 12:
            return None
        if ampm == "am":
            hh = 0 if h == 12 else h
        else:
            hh = 12 if h == 12 else h + 12
    else:
        if h < 0 or h > 23:
            return None
        hh = h
    return time(hour=hh, minute=mi)


def parse_time_token(token: str) -> time | None:
    """Public wrapper (used by local command layer)."""
    return _parse_time_token(token)


def parse_when(
    *,
    date_token: str,
    time_token: str,
    duration_minutes: int = 60,
    now: datetime | None = None,
) -> ParsedWhen | None:
    d = _parse_date_token(date_token, now=now)
    t = _parse_time_token(time_token)
    if d is None or t is None:
        return None
    start = datetime.combine(d, t)
    end = start + timedelta(minutes=max(1, int(duration_minutes)))
    return ParsedWhen(start=start, end=end)


def _osascript(source: str) -> tuple[int, str]:
    r = subprocess.run(
        ["osascript", "-e", source],
        check=False,
        capture_output=True,
        text=True,
    )
    out = (r.stdout or r.stderr or "").strip()
    return r.returncode, out


def _as_applescript_date_expr(dt: datetime, *, var_name: str) -> str:
    """
    Build an AppleScript snippet that sets ``var_name`` to a date with explicit components.
    This avoids locale-dependent string parsing in AppleScript's ``date "..."``.
    """
    return (
        f"set {var_name} to (current date)\n"
        f"set year of {var_name} to {dt.year}\n"
        f"set month of {var_name} to {dt.month}\n"
        f"set day of {var_name} to {dt.day}\n"
        f"set hours of {var_name} to {dt.hour}\n"
        f"set minutes of {var_name} to {dt.minute}\n"
        f"set seconds of {var_name} to {dt.second}\n"
    )


def _escape_applescript_string(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


def create_event(
    *,
    title: str,
    start: datetime,
    end: datetime,
    calendar_name: str | None = None,
) -> tuple[bool, str | None]:
    if sys.platform != "darwin":
        return False, "Calendar commands are only supported on macOS."
    summary = (title or "").strip()
    if not summary:
        return False, "Missing event title."

    if (calendar_name or "").strip():
        cal_pick = f'first calendar whose name is "{_escape_applescript_string(calendar_name or "")}"'
    else:
        cal_pick = "calendar 1"
    safe_title = _escape_applescript_string(summary)
    script = f'''
    {_as_applescript_date_expr(start, var_name="startDate")}
    {_as_applescript_date_expr(end, var_name="endDate")}
    tell application "Calendar"
        set theCal to {cal_pick}
        make new event at end of events of theCal with properties {{summary:"{safe_title}", start date:startDate, end date:endDate}}
        return "ok"
    end tell
    '''
    code, out = _osascript(script)
    if code != 0:
        return False, (out or None)
    return True, None


def delete_events_on_day(
    *,
    title_contains: str,
    day: date,
    limit: int = 1,
) -> tuple[int, str | None]:
    """
    Delete up to ``limit`` events whose summary contains ``title_contains`` and whose
    start date occurs on the local calendar day ``day``. Returns deleted count.
    """
    if sys.platform != "darwin":
        return 0, "Calendar commands are only supported on macOS."
    needle = (title_contains or "").strip()
    if not needle:
        return 0, "Missing event title."
    safe_needle = _escape_applescript_string(needle)

    start = datetime.combine(day, time(0, 0, 0))
    end = start + timedelta(days=1)
    lim = max(1, int(limit))

    script = f'''
    {_as_applescript_date_expr(start, var_name="dayStart")}
    {_as_applescript_date_expr(end, var_name="dayEnd")}
    set deletedCount to 0
    tell application "Calendar"
        repeat with cal in (every calendar)
            set evs to (every event of cal whose summary contains "{safe_needle}" and start date is greater than or equal to dayStart and start date is less than dayEnd)
            repeat with ev in evs
                delete ev
                set deletedCount to deletedCount + 1
                if deletedCount is greater than or equal to {lim} then
                    return deletedCount as string
                end if
            end repeat
        end repeat
    end tell
    return deletedCount as string
    '''
    code, out = _osascript(script)
    if code != 0:
        return 0, (out or None)
    try:
        return int(out.strip() or "0"), None
    except ValueError:
        return 0, None

