"""Spoken commands handled locally (no LLM) while in a Luna session."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from jarvis.integrations import calendar_events, cursor_mode, notion, spotify, youtube
from jarvis.services import weather as weather_service
from jarvis.services.calendar import schedule_speech_for_local_date

_OPEN_NOTION_RE = re.compile(
    r"\b(open|launch|start|show|bring up)\s+(?:the\s+)?notion(?:\s+app)?\b"
    r"|\bnotion\s+(?:please|open)\b",
    re.IGNORECASE,
)

_OPEN_YOUTUBE_RE = re.compile(
    r"\b(open|launch|start|show|bring up)\s+(?:the\s+)?(?:youtube|you\s*tube|you\s+tube)\b"
    r"|\byoutube\s+(?:please|open)\b",
    re.IGNORECASE,
)

_SPOTIFY_TOGGLE_RE = re.compile(r"\b(play\s*pause|toggle)\b", re.IGNORECASE)
_SPOTIFY_PLAY_RE = re.compile(r"\b(play|resume)\b", re.IGNORECASE)
_SPOTIFY_PAUSE_RE = re.compile(r"\b(pause|stop)\b", re.IGNORECASE)
_SPOTIFY_NEXT_RE = re.compile(r"\b(next|skip)\b", re.IGNORECASE)
_SPOTIFY_PREV_RE = re.compile(r"\b(previous|prev|back)\b", re.IGNORECASE)

_SPOTIFY_MUSIC_CONTEXT_RE = re.compile(r"\b(spotify|music|song|track)\b", re.IGNORECASE)

_WEATHER_RE = re.compile(
    r"\b("
    r"what'?s\s+the\s+weather|"
    r"how'?s\s+the\s+weather|"
    r"current\s+weather|"
    r"weather\s+(?:like|now|outside|today)|"
    r"tell\s+me\s+the\s+weather"
    r")\b",
    re.IGNORECASE,
)

_TIME_RE = re.compile(
    r"\b("
    r"what\s+time\s+is\s+it|"
    r"what'?s\s+the\s+time|"
    r"current\s+time|"
    r"what\s+is\s+the\s+time|"
    r"tell\s+me\s+the\s+time"
    r")\b",
    re.IGNORECASE,
)

_LOCATION_RE = re.compile(
    r"\b("
    r"where\s+am\s+i|"
    r"my\s+location|"
    r"current\s+location|"
    r"where\s+are\s+we|"
    r"what\s+city\s+am\s+i\s+in"
    r")\b",
    re.IGNORECASE,
)

# ASR often hears "curse" / "curser" instead of "Cursor" (the IDE).
_CUSR = r"(?:cursor|curse|curser)"

_SCHEDULE_READ_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"what'?s\s+on(?:\s+my\s+calendar|\s+the\s+calendar)?|"
    r"what\s+do\s+i\s+have|"
    r"\b(?:any\s+)?(?:events?|meetings?|appointments?)\s+(?:on|for)\s+(?:today|tomorrow|\d{4}-\d{2}-\d{2})\b|"
    r"\b(?:events?|meetings?|appointments?)\s+(?:today|tomorrow)\b|"
    r"\b(?:my\s+)?(?:calendar|schedule)\s+(?:for|on)\s+(?:today|tomorrow|\d{4}-\d{2}-\d{2})\b|"
    r"\b(?:my\s+)?(?:calendar|schedule)\s+(?:today|tomorrow)\b"
    r")",
)

_CURSOR_MODE_ACTIVATE_RE = re.compile(
    rf"(?i)\b("
    rf"activate\s+mode\s+{_CUSR}|"
    rf"activate\s+{_CUSR}\s+mode|"
    rf"activate\s+(?:\w+\s+){{0,8}}{_CUSR}(?:\s+mode)?|"
    rf"enable\s+{_CUSR}\s+mode|"
    rf"start\s+{_CUSR}\s+mode|"
    rf"open\s+{_CUSR}\s+mode|"
    rf"open\s+mode\s+{_CUSR}|"
    rf"{_CUSR}\s+mode\s+(?:on|please)"
    rf")\b",
)

_CURSOR_READ_FULL_RE = re.compile(
    rf"(?i)\b(read|speak|say|voice)\s+(?:the\s+)?full\s+{_CUSR}\s+(?:reply|response|answer)\b",
)

_CURSOR_READ_BRIEF_RE = re.compile(
    rf"(?i)\b("
    rf"read\s+{_CUSR}\s+(?:summary|conclusion)|"
    rf"brief\s+{_CUSR}\s+(?:reply|response|answer)|"
    rf"{_CUSR}\s+(?:summary|conclusion)"
    rf")\b",
)

_CURSOR_READ_REPLY_RE = re.compile(
    rf"(?i)\b(read|speak|say|voice)\s+(?:the\s+)?{_CUSR}\s+(?:reply|response|answer)\b",
)

# Strict: "volume 50" / "set volume to 50"
_SET_VOLUME_DIGITS_RE = re.compile(
    r"\b(?:set\s+)?(?:spotify\s+)?volume\s+(?:to\s+)?(?P<value>\d{1,3})\s*(?:%|percent)?\b",
    re.IGNORECASE,
)
# ASR inserts filler ("uh", "like") or reorders slightly — still grab the first 1–3 digit percent.
_SET_VOLUME_DIGITS_LOOSE_RE = re.compile(
    r"\b(?:set\s+)?(?:spotify\s+)?volume\b(?:\s+\w+){0,5}\s+(?:to\s+)?(?P<value>\d{1,3})\s*(?:%|percent)?\b",
    re.IGNORECASE,
)
# "volume 3 5" → 35 (common digit splitting from Whisper/Vosk).
_SET_VOLUME_SPLIT_DIGITS_RE = re.compile(
    r"\b(?:set\s+)?(?:spotify\s+)?volume\b(?:\s+\w+){0,4}\s+(?:to\s+)?"
    r"(?P<a>[0-9])\s+(?P<b>[0-9])\s*(?:%|percent)?\b",
    re.IGNORECASE,
)
_VOLUME_UP_RE = re.compile(
    r"\b("
    r"volume\s+up|"
    r"louder|"
    r"increase\s+volume|"
    r"turn\s+it\s+up|"
    r"turn\s+up\s+the\s+volume|"
    r"turn\s+up\s+volume"
    r")\b",
    re.IGNORECASE,
)
_VOLUME_DOWN_RE = re.compile(
    r"\b("
    r"volume\s+down|"
    r"quieter|"
    r"decrease\s+volume|"
    r"turn\s+it\s+down|"
    r"turn\s+down\s+the\s+volume|"
    r"turn\s+down\s+volume"
    r")\b",
    re.IGNORECASE,
)

_VOL_WORD_CORE = (
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
    r"seventy|eighty|ninety|hundred|maximum|max"
)
# Whole words only — otherwise "two" matches as a prefix of "twenty".
_VOL_WORD_ALT = rf"\b(?:{_VOL_WORD_CORE})\b"
_SET_VOLUME_WORDS_RE = re.compile(
    rf"\b(?:set\s+)?(?:spotify\s+)?volume\b(?:\s+(?:uh|um|like|please)){{0,2}}\s+(?:to\s+)?"
    rf"(?P<words>{_VOL_WORD_ALT}\s+{_VOL_WORD_ALT}|{_VOL_WORD_ALT})(?:\s+percent)?\b",
    re.IGNORECASE,
)


_NUM_WORDS_0_19: dict[str, int] = {
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
}
_TENS_WORDS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_VOL_PAIR_WORDS: dict[str, int] = {
    w: _NUM_WORDS_0_19[w]
    for w in (
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
    )
}
_VOL_PAIR_WORDS["oh"] = 0

_SINGLE_DIGIT_VOL = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_SET_VOLUME_SPLIT_WORDS_RE = re.compile(
    rf"\b(?:set\s+)?(?:spotify\s+)?volume\b(?:\s+\w+){{0,4}}\s+(?:to\s+)?"
    rf"(?P<a>{_SINGLE_DIGIT_VOL})\s+(?P<b>{_SINGLE_DIGIT_VOL})\s*(?:%|percent)?\b",
    re.IGNORECASE,
)


def _pair_vol_words_to_percent(a: str, b: str) -> int | None:
    ia = _VOL_PAIR_WORDS.get(a.strip().lower())
    ib = _VOL_PAIR_WORDS.get(b.strip().lower())
    if ia is None or ib is None:
        return None
    return ia * 10 + ib


def _parse_0_100_words(s: str) -> int | None:
    """
    Best-effort spoken number parser for 0–100.
    Handles: "ten", "twenty", "twenty five", "one hundred", "hundred", "max", "maximum".
    """
    raw = (s or "").strip().lower().replace("-", " ")
    if not raw:
        return None
    if raw in ("max", "maximum"):
        return 100
    if raw in ("hundred", "one hundred"):
        return 100
    parts = [p for p in raw.split() if p and p != "percent"]
    if not parts:
        return None
    if len(parts) == 1:
        if parts[0] in _NUM_WORDS_0_19:
            return _NUM_WORDS_0_19[parts[0]]
        if parts[0] in _TENS_WORDS:
            return _TENS_WORDS[parts[0]]
        return None
    if len(parts) == 2:
        # "twenty five"
        if parts[0] in _TENS_WORDS and parts[1] in _NUM_WORDS_0_19:
            return _TENS_WORDS[parts[0]] + _NUM_WORDS_0_19[parts[1]]
        # "one hundred"
        if parts[0] == "one" and parts[1] == "hundred":
            return 100
        # Common STT mis-hear for "twenty five" ("volume two fifty percent").
        if parts[0] == "two" and parts[1] == "fifty":
            return 25
    return None

_ADD_EVENT_ON_AT_RE = re.compile(
    r"^\s*(?:add|create|schedule)\s+(?:the\s+)?(?:a\s+)?(?:calendar\s+)?events?\s+"
    r"(?P<title>.+?)\s+on\s+(?P<date>today|tomorrow|\d{4}-\d{2}-\d{2})\s+at\s+(?P<time>[\w\dapm:\.\s]+)\s*$",
    re.IGNORECASE,
)
_ADD_EVENT_AT_ON_RE = re.compile(
    r"^\s*(?:add|create|schedule)\s+(?:the\s+)?(?:a\s+)?(?:calendar\s+)?events?\s+"
    r"(?P<title>.+?)\s+at\s+(?P<time>[\w\dapm:\.\s]+)\s+on\s+(?P<date>today|tomorrow|\d{4}-\d{2}-\d{2})\s*$",
    re.IGNORECASE,
)
_ADD_EVENT_AT_RE = re.compile(
    r"^\s*(?:add|create|schedule)\s+(?:the\s+)?(?:a\s+)?(?:calendar\s+)?events?\s+"
    r"(?P<title>.+?)\s+at\s+(?P<time>[\w\d:\.\s]+)\s*$",
    re.IGNORECASE,
)
_DELETE_EVENT_RE = re.compile(
    r"^\s*(?:delete|remove|cancel)\s+(?:the\s+)?(?:calendar\s+)?events?\s+"
    r"(?P<title>.+?)\s+on\s+(?P<date>today|tomorrow|\d{4}-\d{2}-\d{2})\s*$",
    re.IGNORECASE,
)

_REMINDER_RE = re.compile(
    r"(?i)\b(?:remind\s+me|set\s+(?:a\s+)?(?:timer|reminder)|timer)\s+"
    r"(?:for\s+|in\s+)"
    r"(?P<n>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>sec(?:ond)?s?|min(?:ute)?s?|h(?:ou)?rs?)\b",
)

_CLIPBOARD_RE = re.compile(
    r"(?i)\b("
    r"what(?:'?s|\s+is)\s+(?:in\s+)?(?:my\s+)?clipboard|"
    r"read\s+(?:my\s+)?clipboard|"
    r"read\s+clipboard|"
    r"clipboard\s+contents?"
    r")\b",
)

_DATE_TOKEN_IN_TEXT_RE = re.compile(r"\b(today|tomorrow|\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_ADD_VERB_RE = re.compile(r"\b(add|create|schedule)\b", re.IGNORECASE)
_EVENT_WORD_RE = re.compile(r"\b(event|events|meeting|appointment)\b", re.IGNORECASE)
_CALENDAR_WORD_RE = re.compile(r"\bcalendar\b", re.IGNORECASE)
_CALLED_RE = re.compile(r"\b(called|named|titled|call it)\b", re.IGNORECASE)
_NEED_ASSIST_RE = re.compile(r"\b(need you to|i need you to|can you|could you|please)\b", re.IGNORECASE)


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", (text or "").strip()) if t]


def _find_times_in_text(text: str) -> list[tuple[int, int, str]]:
    """
    Return list of (start_token_idx, end_token_idx_exclusive, raw_time_token).
    Attempts windows of 1–3 tokens and keeps earliest non-overlapping matches.
    """
    toks = _tokenize(text)
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(toks):
        hit = None
        for w in (3, 2, 1):
            if i + w > len(toks):
                continue
            chunk = " ".join(toks[i : i + w])
            if calendar_events.parse_time_token(chunk) is not None:
                hit = (i, i + w, chunk)
                break
        if hit is None:
            i += 1
            continue
        out.append(hit)
        i = hit[1]
        if len(out) >= 2:
            break
    return out


def _extract_add_event_fields(text: str) -> tuple[str | None, str | None, str | None, str | None]:
    """
    Returns (title, date_token, start_time_token, end_time_token).
    Title may be None if not confidently extractable.
    """
    raw = (text or "").strip()
    if not raw:
        return None, None, None, None
    if not (_EVENT_WORD_RE.search(raw) or _CALENDAR_WORD_RE.search(raw)):
        return None, None, None, None
    # Sometimes Vosk drops "add" (or mishears it). Accept either an explicit add verb,
    # or a clear assistance phrase when "event" is present.
    if not (_ADD_VERB_RE.search(raw) or _NEED_ASSIST_RE.search(raw)):
        return None, None, None, None

    mdate = _DATE_TOKEN_IN_TEXT_RE.search(raw)
    date_token = (mdate.group(1).lower() if mdate else None)

    times = _find_times_in_text(raw)
    start_time = times[0][2] if len(times) >= 1 else None
    end_time = times[1][2] if len(times) >= 2 else None

    mcalled = _CALLED_RE.search(raw)
    if mcalled:
        title = raw[mcalled.end() :].strip(' "\'.,')
        return (title or None), date_token, start_time, end_time

    # Heuristic: remove date token + time chunks + common boilerplate, what's left is the title.
    scrub = raw
    if mdate:
        scrub = scrub.replace(mdate.group(0), " ")
    for _s, _e, chunk in times:
        scrub = re.sub(rf"\b{re.escape(chunk)}\b", " ", scrub, flags=re.IGNORECASE)
    scrub = re.sub(
        r"\b(add|create|schedule|calendar|event|events|meeting|appointment|for|on|at|from|to|until|need|i|you|please|want)\b",
        " ",
        scrub,
        flags=re.IGNORECASE,
    )
    scrub = re.sub(r"\b(an|a|the|me|my|to)\b", " ", scrub, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", scrub).strip(' "\'.,')
    if not title:
        return None, date_token, start_time, end_time
    return title, date_token, start_time, end_time


def try_parse_add_event_missing_date(text: str) -> tuple[str, str, str | None] | None:
    title, date_token, start_time, end_time = _extract_add_event_fields(text)
    if not title or date_token or not start_time:
        return None
    return title, start_time, end_time


def try_parse_add_event_partial(text: str) -> tuple[str | None, str | None, str | None, str | None] | None:
    """
    Return (title, date_token, start_time_token, end_time_token) if this looks like an add-event request
    but is missing some fields. Otherwise None.
    """
    title, date_token, start_time, end_time = _extract_add_event_fields(text)
    if title is None and date_token is None and start_time is None and end_time is None:
        return None
    if title and date_token and start_time:
        return None
    return title, date_token, start_time, end_time


def _try_schedule_read_command(raw: str) -> tuple[bool, str | None] | None:
    """List calendar events for a day (today if no date said). Returns None if not a schedule query."""
    if re.search(r"\b(delete|remove|cancel)\b", raw, re.IGNORECASE):
        return None
    if re.search(r"\b(add|create)\s+", raw, re.IGNORECASE) and re.search(r"\bevents?\b", raw, re.IGNORECASE):
        return None
    if not _SCHEDULE_READ_RE.search(raw):
        return None
    m = _DATE_TOKEN_IN_TEXT_RE.search(raw)
    if m:
        d = calendar_events.parse_date_token(m.group(1))
        if d is None:
            return True, "I couldn't parse which day you meant."
        return True, schedule_speech_for_local_date(d)
    d0 = datetime.now().astimezone().date()
    return True, schedule_speech_for_local_date(d0)


def try_voice_command(
    text: str,
    *,
    notion_app: str = "Notion",
    notion_fullscreen: bool = False,
    duration_decider: Callable[[str], int] | None = None,
    speak_fn: Callable[[str], None] | None = None,
) -> tuple[bool, str | None, bool]:
    """
    If ``text`` matches a known command, return ``(True, short_spoken_reply, release_mic)``.
    ``release_mic`` is True for Cursor AI handoff so Luna stops capturing the mic.
    Otherwise ``(False, None, False)``. Reply is for Kokoro (keep short).
    ``speak_fn`` is used for async callbacks like timers.
    """
    raw = (text or "").strip()
    if not raw:
        return False, None, False

    # Timer / reminder
    m_timer = _REMINDER_RE.search(raw)
    if m_timer:
        n = float(m_timer.group("n"))
        unit = m_timer.group("unit").lower()
        if unit.startswith("h"):
            secs = n * 3600
            label = f"{int(n)} hour{'s' if n != 1 else ''}"
        elif unit.startswith("m"):
            secs = n * 60
            label = f"{int(n)} minute{'s' if n != 1 else ''}"
        else:
            secs = n
            label = f"{int(n)} second{'s' if n != 1 else ''}"
        secs = max(1.0, min(7200.0, secs))

        def _fire(msg: str, fn: Callable[[str], None] | None) -> None:
            if fn:
                try:
                    fn(msg)
                except Exception:
                    pass

        msg = f"Time's up! Your {label} timer has finished."
        t = threading.Timer(secs, _fire, args=[msg, speak_fn])
        t.daemon = True
        t.start()
        return True, f"Okay, I'll remind you in {label}.", False

    # Clipboard read
    if _CLIPBOARD_RE.search(raw):
        ok, spoken = cursor_mode.clipboard_text_for_cursor_reply(brief=True)
        return True, spoken, False

    # Spotify/music controls (local + instant).
    # We require a "music context" word for ambiguous verbs like play/stop/back to reduce false positives.
    # Split-digit/word must run before a single \d{1,3} match so "volume 3 5" → 35, not 3.
    m_vol_split_d = _SET_VOLUME_SPLIT_DIGITS_RE.search(raw)
    if m_vol_split_d:
        v = int(m_vol_split_d.group("a")) * 10 + int(m_vol_split_d.group("b"))
        v = max(0, min(100, v))
        ok = spotify.set_sound_volume(v)
        if ok:
            return True, f"Setting volume to {v}.", False
        return True, "I couldn't set Spotify volume.", False

    m_vol_split_w = _SET_VOLUME_SPLIT_WORDS_RE.search(raw)
    if m_vol_split_w:
        vw = _pair_vol_words_to_percent(m_vol_split_w.group("a"), m_vol_split_w.group("b"))
        if vw is not None:
            vw = max(0, min(100, int(vw)))
            ok = spotify.set_sound_volume(vw)
            if ok:
                return True, f"Setting volume to {vw}.", False
            return True, "I couldn't set Spotify volume.", False

    m_vol_digits = _SET_VOLUME_DIGITS_RE.search(raw)
    if not m_vol_digits:
        m_vol_digits = _SET_VOLUME_DIGITS_LOOSE_RE.search(raw)
    if m_vol_digits:
        v = int(m_vol_digits.group("value"))
        v = max(0, min(100, v))
        ok = spotify.set_sound_volume(v)
        if ok:
            return True, f"Setting volume to {v}.", False
        return True, "I couldn't set Spotify volume.", False

    m_vol_words = _SET_VOLUME_WORDS_RE.search(raw)
    if m_vol_words:
        v2 = _parse_0_100_words(m_vol_words.group("words"))
        if v2 is None:
            return True, "I couldn't parse that volume.", False
        v2 = max(0, min(100, int(v2)))
        ok = spotify.set_sound_volume(v2)
        if ok:
            return True, f"Setting volume to {v2}.", False
        return True, "I couldn't set Spotify volume.", False

    if _VOLUME_UP_RE.search(raw):
        cur = spotify.get_sound_volume()
        if cur is None:
            return True, "I couldn't read Spotify volume.", False
        target = min(100, cur + 10)
        ok = spotify.set_sound_volume(target)
        return True, (f"Volume {target}." if ok else "I couldn't set Spotify volume."), False

    if _VOLUME_DOWN_RE.search(raw):
        cur = spotify.get_sound_volume()
        if cur is None:
            return True, "I couldn't read Spotify volume.", False
        target = max(0, cur - 10)
        ok = spotify.set_sound_volume(target)
        return True, (f"Volume {target}." if ok else "I couldn't set Spotify volume."), False

    if _SPOTIFY_MUSIC_CONTEXT_RE.search(raw):
        if _SPOTIFY_TOGGLE_RE.search(raw):
            ok = spotify.play_pause()
            return True, ("Toggling playback." if ok else "I couldn't control Spotify."), False
        if _SPOTIFY_NEXT_RE.search(raw):
            ok = spotify.next_track()
            if ok:
                import time as _time; _time.sleep(0.6)
                track = spotify.get_current_track()
                reply = f"Next. Now playing {track}." if track else "Next track."
            else:
                reply = "I couldn't control Spotify."
            return True, reply, False
        if _SPOTIFY_PREV_RE.search(raw):
            ok = spotify.previous_track()
            if ok:
                import time as _time; _time.sleep(0.6)
                track = spotify.get_current_track()
                reply = f"Going back. Now playing {track}." if track else "Previous track."
            else:
                reply = "I couldn't control Spotify."
            return True, reply, False
        if _SPOTIFY_PAUSE_RE.search(raw):
            ok = spotify.pause()
            return True, ("Pausing." if ok else "I couldn't control Spotify."), False
        # "play music" / "resume music"
        if _SPOTIFY_PLAY_RE.search(raw):
            ok = spotify.play()
            if ok:
                import time as _time; _time.sleep(0.6)
                track = spotify.get_current_track()
                reply = f"Playing {track}." if track else "Playing."
            else:
                reply = "I couldn't control Spotify."
            return True, reply, False

    sched = _try_schedule_read_command(raw)
    if sched is not None:
        s_ok, s_msg = sched
        return s_ok, s_msg, False

    if _WEATHER_RE.search(raw):
        return True, weather_service.compose_current_weather_brief_speech(), False

    if _TIME_RE.search(raw):
        return True, weather_service.spoken_local_time_now(), False

    if _LOCATION_RE.search(raw):
        return True, weather_service.spoken_location_brief(), False

    if _CURSOR_READ_FULL_RE.search(raw):
        _ok_c, text_c = cursor_mode.clipboard_text_for_cursor_reply(brief=False)
        return True, text_c, False

    if _CURSOR_READ_BRIEF_RE.search(raw) or _CURSOR_READ_REPLY_RE.search(raw):
        _ok_c, text_c = cursor_mode.clipboard_text_for_cursor_reply(brief=True)
        return True, text_c, False

    if _CURSOR_MODE_ACTIVATE_RE.search(raw):
        _ok_c, msg_c = cursor_mode.activate_cursor_mode(with_dictation=False)
        return True, msg_c, True

    if _OPEN_NOTION_RE.search(raw):
        notion.open_notion(app_name=notion_app, try_fullscreen=notion_fullscreen)
        return True, "Opening Notion.", False

    if _OPEN_YOUTUBE_RE.search(raw):
        youtube.open_youtube()
        return True, "Opening YouTube.", False

    title, date_token, start_time, end_time = _extract_add_event_fields(raw)
    if title or date_token or start_time or end_time:
        if not title or not date_token or not start_time:
            # Let the chat layer do slot filling (date/title/time).
            return False, None, False
        d = calendar_events.parse_date_token(date_token)
        st = calendar_events.parse_time_token(start_time)
        et = calendar_events.parse_time_token(end_time) if end_time else None
        if d is None or st is None:
            return True, "I couldn't parse that. Say: add event Work on today at 4 PM.", False
        start_dt = datetime.combine(d, st)
        if et is not None:
            end_dt = datetime.combine(d, et)
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(minutes=60)
        else:
            mins = 60
            if duration_decider is not None:
                try:
                    mins = int(duration_decider(title))
                except Exception:
                    mins = 60
            mins = max(15, min(240, mins))
            end_dt = start_dt + timedelta(minutes=mins)

        ok, err = calendar_events.create_event(title=title, start=start_dt, end=end_dt)
        if ok:
            return True, "Event added.", False
        if err and ("not authorized" in err.lower() or "not authorised" in err.lower()):
            return True, "I need Calendar permission for Terminal or Cursor.", False
        return True, "I couldn't add that event.", False

    m = _DELETE_EVENT_RE.match(raw)
    if m:
        title = (m.group("title") or "").strip(' "\'')
        d = calendar_events.parse_date_token(m.group("date") or "")
        if d is None or not title:
            return True, "I couldn't parse that. Say: delete event title on 2026-04-17.", False
        n, err = calendar_events.delete_events_on_day(title_contains=title, day=d, limit=1)
        if n >= 1:
            return True, "Event deleted.", False
        if err and ("not authorized" in err.lower() or "not authorised" in err.lower()):
            return True, "I need Calendar permission for Terminal or Cursor.", False
        return True, "I couldn't find that event.", False

    m_list_plans = re.search(
        r"\b(list|show|what|read)[\w\s]*(my\s+)?plans?\b",
        raw, re.IGNORECASE
    )
    if m_list_plans and not re.search(r"\b(create|make|write|generate|build|draft)\b", raw, re.IGNORECASE):
        try:
            from jarvis.services.plans import list_plans
            names = list_plans()
        except Exception:
            names = []
        if not names:
            return True, "You don't have any plans yet. Say 'create a plan for' followed by a topic.", False
        if len(names) == 1:
            return True, f"You have one plan: {names[0].replace('-', ' ')}.", False
        listed = ", ".join(n.replace("-", " ") for n in names[:-1])
        return True, f"You have {len(names)} plans: {listed}, and {names[-1].replace('-', ' ')}.", False

    m_del_plan = re.search(r"\b(delete|remove)\s+(?:the\s+)?plan\s+(?:for\s+|called\s+|named\s+)?(.+)", raw, re.IGNORECASE)
    if m_del_plan:
        plan_name = m_del_plan.group(2).strip().rstrip(".")
        try:
            from jarvis.services.plans import delete_plan
            ok = delete_plan(plan_name)
        except Exception:
            ok = False
        reply = f"Deleted the plan for {plan_name}." if ok else f"I couldn't find a plan called {plan_name}."
        return True, reply, False

    if re.search(
        r"\b(what\s+can\s+you\s+do|what\s+are\s+your\s+(commands|capabilities)|help\s+me|show\s+commands)\b",
        raw,
        re.IGNORECASE,
    ):
        return (
            True,
            "I can control Spotify — play, pause, next, previous, and set volume. "
            "I can check the weather, time, and your calendar. "
            "I can add or delete calendar events and set reminders. "
            "For anything else, just ask and I'll answer using AI.",
            False,
        )

    return False, None, False

