"""
Today's Calendar.app events for the spoken briefing (macOS).

Uses **EventKit** when available so **recurring** events (ORGB, weekly lectures, etc.)
appear with correct times for today. Falls back to AppleScript if EventKit is
unavailable or access is denied.

Timed events that **start today** (local) and **all-day** events for today are
listed; when nothing remains today, the briefing can list **tomorrow** instead.
**Recurring** events need EventKit with Calendars access already granted
for the Python host (see one-time stderr hint); otherwise AppleScript lists
non-recurring events only.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

# One-time hint when EventKit is not pre-authorized (recurring events need it).
_eventkit_hint_printed = False


@dataclass(frozen=True)
class _CalEvent:
    start: datetime
    end: datetime
    summary: str
    all_day: bool


def _speech_time(dt: datetime) -> str:
    """12-hour clock for speech, e.g. 2:05 PM (no leading zero on hour)."""
    h24 = dt.hour
    if h24 == 0:
        h12, suf = 12, "AM"
    elif h24 < 12:
        h12, suf = h24, "AM"
    elif h24 == 12:
        h12, suf = 12, "PM"
    else:
        h12, suf = h24 - 12, "PM"
    if dt.minute == 0:
        return f"{h12} {suf}"
    return f"{h12}:{dt.minute:02d} {suf}"


def _calendar_date(d: datetime) -> str:
    return f"{d.strftime('%B')} {d.day}"


def _event_time_phrase(ev: _CalEvent) -> str:
    """Natural start/end fragment for speech."""
    if ev.all_day:
        if ev.end <= ev.start:
            return f"All day on {_calendar_date(ev.start)}"
        # ``end`` is often exclusive (midnight after last day).
        last = ev.end - timedelta(seconds=1)
        if last.date() <= ev.start.date():
            return f"All day on {_calendar_date(ev.start)}"
        return f"All day from {_calendar_date(ev.start)} through {_calendar_date(last)}"
    if ev.end <= ev.start:
        return f"at {_speech_time(ev.start)}"
    return f"from {_speech_time(ev.start)} to {_speech_time(ev.end)}"


def _ns_to_local_dt(ns: Any) -> datetime:
    from Foundation import NSDate

    if not isinstance(ns, NSDate):
        raise TypeError(type(ns))
    tz = datetime.now().astimezone().tzinfo
    return datetime.fromtimestamp(float(ns.timeIntervalSince1970()), tz=tz)


def _local_day_bounds() -> tuple[datetime, datetime, datetime, Any]:
    """``(now, start_today, end_today, tz)`` in local timezone."""
    now = datetime.now().astimezone()
    tz = now.tzinfo
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return now, start, end, tz


def _dt_to_ns(dt: datetime) -> Any:
    from Foundation import NSDate

    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def _eventkit_can_read_events() -> bool:
    """
    Use EventKit only when access is **already** granted.

    We do **not** request permission from this script (that blocks for seconds and
    often fails from a background thread). Turn on **Settings → Privacy & Security
    → Calendars** for ``python3`` / Terminal so status becomes authorized, then
    recurring events (ORGB, etc.) load correctly.
    """
    global _eventkit_hint_printed

    from EventKit import EKEntityTypeEvent, EKEventStore

    et = EKEntityTypeEvent
    status = int(EKEventStore.authorizationStatusForEntityType_(et))
    if status in (3, 4):
        return True
    if status == 0 and not _eventkit_hint_printed:
        _eventkit_hint_printed = True
        print(
            "Jarvis: Calendar list is incomplete until EventKit is allowed. "
            "System Settings → Privacy & Security → Calendars → enable for the app "
            "that runs this script (e.g. Terminal). Then recurring events appear.",
            file=sys.stderr,
        )
    return False


def _fetch_via_eventkit_range(d0: datetime, d1: datetime) -> list[_CalEvent] | None:
    """Return events overlapping ``[d0, d1)`` including recurrences, or None on failure."""
    try:
        from EventKit import EKEventStore
    except ImportError:
        return None

    _, _, _, tz = _local_day_bounds()
    if not _eventkit_can_read_events():
        return None
    store = EKEventStore.alloc().init()

    # ``None`` = all calendars (matches Calendar.app); a manual list can miss accounts.
    pred = store.predicateForEventsWithStartDate_endDate_calendars_(
        _dt_to_ns(d0),
        _dt_to_ns(d1),
        None,
    )
    raw = store.eventsMatchingPredicate_(pred)
    out: list[_CalEvent] = []
    for ev in raw:
        try:
            title = str(ev.title() or "Event").replace("\t", " ").replace("\n", " ").strip() or "Event"
            all_day = bool(ev.isAllDay())
            start = _ns_to_local_dt(ev.startDate())
            end = _ns_to_local_dt(ev.endDate())
            if all_day:
                start = datetime(start.year, start.month, start.day, 0, 0, tzinfo=tz)
                end = datetime(end.year, end.month, end.day, 0, 0, tzinfo=tz)
            if end < start:
                end = start + timedelta(hours=1)
            out.append(_CalEvent(start=start, end=end, summary=title, all_day=all_day))
        except (TypeError, ValueError, OSError, AttributeError):
            continue
    return out


def _fetch_via_eventkit() -> list[_CalEvent] | None:
    """Return events for ``[start_today, end_today)`` including recurrences, or None on failure."""
    _, d0, d1, _ = _local_day_bounds()
    return _fetch_via_eventkit_range(d0, d1)


def _run_osascript(source: str) -> tuple[str | None, bool]:
    if sys.platform != "darwin":
        return None, False
    r = subprocess.run(
        ["osascript", "-e", source],
        check=False,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if err:
            print(f"Calendar AppleScript: {err}", file=sys.stderr)
        return None, False
    return (r.stdout or "").strip(), True


def _fetch_raw_event_lines(*, day_offset: int = 0) -> tuple[list[str], bool]:
    """AppleScript fallback (recurring masters often wrong for ``today``)."""
    off = int(day_offset)
    if off < 0 or off > 366:
        return [], False
    script = f'''
    tell application "Calendar"
        set nowD to current date
        set d0 to nowD
        set time of d0 to 0
        set d0 to d0 + ({off}) * days
        set d1 to d0 + 1 * days
        set out to ""
        repeat with cal in (every calendar)
            try
                set evs to (every event of cal whose start date is less than d1 and end date is greater than d0)
                repeat with ev in evs
                    set sum to summary of ev
                    if sum is missing value then set sum to "Event"
                    set AppleScript's text item delimiters to tab
                    set ti to text items of sum
                    set AppleScript's text item delimiters to " "
                    set sum to ti as text
                    set AppleScript's text item delimiters to ""
                    if (allday event of ev) then
                        set sd to start date of ev
                        set ed to end date of ev
                        set y to year of sd as text
                        set m to (month of sd as integer) as text
                        set d to day of sd as text
                        set ey to year of ed as text
                        set em to (month of ed as integer) as text
                        set edd to day of ed as text
                        set out to out & "A" & tab & y & tab & m & tab & d & tab & ey & tab & em & tab & edd & tab & sum & return
                    else
                        set sd to start date of ev
                        set ed to end date of ev
                        set y to year of sd as text
                        set m to (month of sd as integer) as text
                        set d to day of sd as text
                        set h to hours of sd as text
                        set mi to minutes of sd as text
                        set eh to hours of ed as text
                        set emi to minutes of ed as text
                        set out to out & "N" & tab & y & tab & m & tab & d & tab & h & tab & mi & tab & eh & tab & emi & tab & sum & return
                    end if
                end repeat
            end try
        end repeat
        return out
    end tell
    '''
    raw, ok = _run_osascript(script)
    if not ok:
        return [], False
    if not raw:
        return [], True
    return [ln.strip() for ln in raw.splitlines() if ln.strip()], True


def _parse_events(lines: list[str], tz: Any) -> list[_CalEvent]:
    out: list[_CalEvent] = []
    for ln in lines:
        parts = ln.split("\t")
        try:
            if parts[0] == "A" and len(parts) >= 8:
                y, mo, d = int(parts[1]), int(parts[2]), int(parts[3])
                ey, emo, ed = int(parts[4]), int(parts[5]), int(parts[6])
                summ = parts[7].strip() or "Event"
                start = datetime(y, mo, d, 0, 0, tzinfo=tz)
                end = datetime(ey, emo, ed, 0, 0, tzinfo=tz)
                if end < start:
                    end = start + timedelta(days=1)
                out.append(_CalEvent(start=start, end=end, summary=summ, all_day=True))
            elif parts[0] == "N" and len(parts) >= 9:
                y, mo, d = int(parts[1]), int(parts[2]), int(parts[3])
                h, mi = int(parts[4]), int(parts[5])
                eh, emi = int(parts[6]), int(parts[7])
                summ = parts[8].strip() or "Event"
                start = datetime(y, mo, d, h, mi, tzinfo=tz)
                end = datetime(y, mo, d, eh, emi, tzinfo=tz)
                if end < start:
                    end = start + timedelta(hours=1)
                out.append(_CalEvent(start=start, end=end, summary=summ, all_day=False))
        except (ValueError, IndexError, OSError):
            continue
    return out


def _fetch_via_applescript(*, day_offset: int = 0) -> list[_CalEvent] | None:
    lines, ok = _fetch_raw_event_lines(day_offset=day_offset)
    if not ok:
        return None
    _, _, _, tz = _local_day_bounds()
    return _parse_events(lines, tz)


def daily_briefing_schedule_md_lines(reference_date: date) -> list[str]:
    """
    Markdown body lines for the briefing **Schedule** section: **Today** and **Tomorrow**.

    Uses the same calendar windows as the spoken briefing (local midnight boundaries).
    ``reference_date`` is used for the tomorrow heading label; calendar queries always use
    the machine's local "today" (expect ``reference_date == date.today()``).
    """
    lines: list[str] = []

    def _append_day(events: list[_CalEvent] | None, empty_today: bool) -> None:
        if events is None:
            lines.append("- Calendar unavailable")
            return
        if not events:
            lines.append("- No events today" if empty_today else "- No events")
            return
        for ev in sorted(events, key=lambda e: e.start):
            if ev.all_day:
                lines.append(f"- {ev.summary} *(all day)*")
            else:
                start = ev.start.strftime("%-I:%M %p")
                end = ev.end.strftime("%-I:%M %p")
                lines.append(f"- **{start} – {end}** {ev.summary}")

    if sys.platform != "darwin":
        lines.append("### Today")
        lines.append("- Calendar only available on macOS")
        return lines

    tomorrow_d = reference_date + timedelta(days=1)
    lines.append("### Today")
    _append_day(_merged_calendar_for_day_offset(0), empty_today=True)
    lines.append("")
    lines.append(f"### Tomorrow — {tomorrow_d.strftime('%A, %B %-d')}")
    _append_day(_merged_calendar_for_day_offset(1), empty_today=False)
    return lines


def _merged_calendar_for_day_offset(day_offset: int) -> list[_CalEvent] | None:
    """Events overlapping the local calendar day at ``day_offset`` from today (0 = today)."""
    _, d0_today, _, _ = _local_day_bounds()
    d0 = d0_today + timedelta(days=day_offset)
    d1 = d0 + timedelta(days=1)
    ek = _fetch_via_eventkit_range(d0, d1)
    se = _fetch_via_applescript(day_offset=day_offset)
    if ek is None and se is None:
        return None
    ek_list = [] if ek is None else ek
    se_list = [] if se is None else se
    if ek_list and se_list:
        return _merge_event_lists(ek_list, se_list)
    if ek_list:
        return ek_list
    if se_list:
        return se_list
    return []


def _merged_calendar_for_local_date(target: date) -> list[_CalEvent] | None:
    """Events overlapping local calendar day ``target`` (EventKit + AppleScript when applicable)."""
    if sys.platform != "darwin":
        return None
    _, start_today, _, tz = _local_day_bounds()
    d0 = datetime.combine(target, time(0, 0), tzinfo=tz)
    d1 = d0 + timedelta(days=1)
    ek = _fetch_via_eventkit_range(d0, d1)
    off = (target - start_today.date()).days
    se: list[_CalEvent] | None = None
    if 0 <= off <= 366:
        se = _fetch_via_applescript(day_offset=off)
    if ek is None and se is None:
        return None
    ek_list = [] if ek is None else ek
    se_list = [] if se is None else se
    if ek_list and se_list:
        return _merge_event_lists(ek_list, se_list)
    if ek_list:
        return ek_list
    if se_list:
        return se_list
    return []


def _ordinal_speech(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def schedule_speech_for_local_date(target: date) -> str:
    """Spoken summary of Calendar.app events on ``target`` (local date)."""
    if sys.platform != "darwin":
        return "Calendar is only available on macOS."
    events = _merged_calendar_for_local_date(target)
    if events is None:
        return "I couldn't read your calendar."
    _, _, _, tz = _local_day_bounds()
    d0 = datetime.combine(target, time(0, 0), tzinfo=tz)
    d1 = d0 + timedelta(days=1)
    bits = _schedule_bits_for_local_day(events, d0, d1)
    weekday = d0.strftime("%A")
    month = d0.strftime("%B")
    ord_d = _ordinal_speech(target.day)
    label = f"{weekday} {month} {ord_d}"
    if not bits:
        return f"You have nothing on your calendar for {label}."
    if len(bits) == 1:
        return f"On {label}: {bits[0]}."
    tail = ", ".join(bits[:-1]) + f", and {bits[-1]}"
    return f"On {label}: {tail}."


def _merge_event_lists(a: list[_CalEvent], b: list[_CalEvent]) -> list[_CalEvent]:
    """Union by start + end + summary + all_day, sorted by start."""
    seen: set[tuple[bool, datetime, datetime, str]] = set()
    out: list[_CalEvent] = []
    for ev in sorted(a + b, key=lambda e: (e.start, e.summary.lower())):
        k = (
            ev.all_day,
            ev.start.replace(second=0, microsecond=0),
            ev.end.replace(second=0, microsecond=0),
            ev.summary,
        )
        if k in seen:
            continue
        seen.add(k)
        out.append(ev)
    return out


def _today_event_buckets(
    events: list[_CalEvent],
) -> tuple[list[_CalEvent], list[_CalEvent], list[_CalEvent], list[_CalEvent]]:
    """
    Return (all_day_today, past_timed, ongoing_timed, upcoming_timed) for the local day.

    - **past_timed**: events ending at/before now
    - **ongoing_timed**: events spanning now
    - **upcoming_timed**: events starting after now
    """
    now_sys, d0, d1, _ = _local_day_bounds()
    today_d = now_sys.date()

    alldays: list[_CalEvent] = []
    past: list[_CalEvent] = []
    ongoing: list[_CalEvent] = []
    upcoming: list[_CalEvent] = []

    seen: set[tuple[bool, datetime, datetime, str]] = set()
    for ev in events:
        key = (
            ev.all_day,
            ev.start.replace(second=0, microsecond=0),
            ev.end.replace(second=0, microsecond=0),
            ev.summary,
        )
        if key in seen:
            continue
        seen.add(key)

        if ev.all_day:
            if ev.start.date() == today_d:
                alldays.append(ev)
            continue

        # Only consider timed events that *start today* for the briefing.
        if not (d0 <= ev.start < d1):
            continue

        if ev.end <= now_sys:
            past.append(ev)
        elif ev.start <= now_sys < ev.end:
            ongoing.append(ev)
        else:
            upcoming.append(ev)

    alldays.sort(key=lambda e: e.summary.lower())
    past.sort(key=lambda e: e.start)
    ongoing.sort(key=lambda e: e.start)
    upcoming.sort(key=lambda e: e.start)
    return alldays, past, ongoing, upcoming


def _plans_intro(bits: list[str]) -> str:
    if not bits:
        return "You have nothing else on your calendar for today. "
    if len(bits) == 1:
        return f"Your plan for today is {bits[0]}. "
    tail = ", ".join(bits[:-1]) + f", and {bits[-1]}"
    return f"Your plans for today include {tail}. "


def _tomorrow_intro(bits: list[str]) -> str:
    if not bits:
        return ""
    if len(bits) == 1:
        return f"Tomorrow, your plan is {bits[0]}. "
    tail = ", ".join(bits[:-1]) + f", and {bits[-1]}"
    return f"Tomorrow, your plans include {tail}. "


def _schedule_bits_for_local_day(events: list[_CalEvent], d0: datetime, d1: datetime) -> list[str]:
    """All-day and timed events that belong to the calendar day ``[d0, d1)``."""
    day_d = d0.date()
    alldays: list[_CalEvent] = []
    timed: list[_CalEvent] = []
    seen: set[tuple[bool, datetime, datetime, str]] = set()
    for ev in sorted(events, key=lambda e: (e.start, e.summary.lower())):
        key = (
            ev.all_day,
            ev.start.replace(second=0, microsecond=0),
            ev.end.replace(second=0, microsecond=0),
            ev.summary,
        )
        if key in seen:
            continue
        seen.add(key)
        if ev.all_day:
            if ev.start.date() == day_d:
                alldays.append(ev)
        elif d0 <= ev.start < d1:
            timed.append(ev)
    alldays.sort(key=lambda e: e.summary.lower())
    timed.sort(key=lambda e: e.start)
    bits = [f"{ev.summary}, {_event_time_phrase(ev)}" for ev in alldays]
    bits.extend(f"{ev.summary}, {_event_time_phrase(ev)}" for ev in timed)
    return bits


def _tomorrow_calendar_append() -> str:
    """Tomorrow's schedule for speech, or empty when there is nothing tomorrow."""
    events_t = _merged_calendar_for_day_offset(1)
    if not events_t:
        return ""
    _, d0_today, d1_today, _ = _local_day_bounds()
    d0 = d0_today + timedelta(days=1)
    d1 = d1_today + timedelta(days=1)
    bits = _schedule_bits_for_local_day(events_t, d0, d1)
    return _tomorrow_intro(bits)


def today_upcoming_calendar_speech() -> str:
    """
    Natural phrase listing today's items (EventKit expands recurrences when allowed).

    When nothing remains today, lists tomorrow instead; returns empty string if there
    is nothing to say for today or tomorrow, or if calendars cannot be read at all.
    """
    if sys.platform != "darwin":
        return ""

    events = _merged_calendar_for_day_offset(0)
    if events is None:
        return ""

    if not events:
        return _tomorrow_calendar_append()

    alldays, _past, ongoing, upcoming = _today_event_buckets(events)

    bits: list[str] = []
    for ev in alldays:
        bits.append(f"{ev.summary}, {_event_time_phrase(ev)}")
    for ev in upcoming:
        bits.append(f"{ev.summary}, {_event_time_phrase(ev)}")

    if bits:
        return _plans_intro(bits)

    if ongoing:
        if len(ongoing) == 1:
            ev = ongoing[0]
            base = (
                f"You're currently in {ev.summary}, {_event_time_phrase(ev)}. "
                f"You have no more events today. "
            )
        else:
            base = "You're currently in an event. You have no more events today. "
        return base + _tomorrow_calendar_append()

    return _tomorrow_calendar_append()
