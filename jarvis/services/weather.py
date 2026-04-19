"""
IP-based city hint and Open-Meteo weather text for a spoken briefing.

Used by the double-clap welcome flow (speech: ``jarvis.services.tts``).
VPNs and some networks can report the wrong city; that is a limitation of IP geolocation.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from jarvis.services.calendar import today_upcoming_calendar_speech

HTTP_TIMEOUT_S = 12
OPEN_METEO_ATTRIBUTION = "Weather data by Open-Meteo.com (https://open-meteo.com)"

# WMO codes that imply meaningful precipitation for a simple “rain soon” hint.
RAIN_OR_WET_CODES = frozenset(
    {
        51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99,
        71, 73, 75, 77, 85, 86,
    }
)


@dataclass(frozen=True)
class GeoResult:
    """Short city for speech; full label for logs if needed."""

    city: str
    full_place: str
    lat: float
    lon: float


def http_get_json(url: str) -> dict[str, Any]:
    req = Request(
        url,
        headers={"User-Agent": "jarvis/1.0 (personal use)"},
        method="GET",
    )
    with urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def wmo_weather_phrase(code: int | None) -> str:
    if code is None:
        return "mixed conditions"
    table: dict[int, str] = {
        0: "clear skies",
        1: "mostly clear skies",
        2: "partly cloudy skies",
        3: "overcast skies",
        45: "fog",
        48: "fog with a bit of rime",
        51: "light drizzle",
        53: "drizzle",
        55: "steady drizzle",
        56: "freezing drizzle",
        57: "heavy freezing drizzle",
        61: "light rain",
        63: "rain",
        65: "heavy rain",
        66: "freezing rain",
        67: "heavy freezing rain",
        71: "light snow",
        73: "snow",
        75: "heavy snow",
        77: "snow grains",
        80: "a few rain showers",
        81: "rain showers",
        82: "heavy rain showers",
        85: "snow showers",
        86: "heavy snow showers",
        95: "a thunderstorm",
        96: "a thunderstorm with a little hail",
        99: "a thunderstorm with hail",
    }
    return table.get(int(code), "mixed conditions")


def ordinal_day(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fetch_ip_location() -> GeoResult | None:
    """Return geolocation using HTTPS IP lookup, or None on failure."""
    try:
        data = http_get_json("https://ipapi.co/json/")
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        print(f"IP location request failed: {e}", file=sys.stderr)
        return None
    if data.get("error"):
        print(f"IP location error: {data.get('reason', data)}", file=sys.stderr)
        return None
    city = data.get("city")
    region = data.get("region")
    country = data.get("country_name") or data.get("country")
    lat = data.get("latitude")
    lon = data.get("longitude")
    if lat is None or lon is None:
        print("IP location response missing coordinates.", file=sys.stderr)
        return None
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        print("IP location coordinates not numeric.", file=sys.stderr)
        return None
    parts = [p for p in (city, region, country) if p]
    full = ", ".join(parts) if parts else "your area"
    speech_city = (str(city).strip() if city else None) or full.split(",")[0].strip() or "your area"
    return GeoResult(city=speech_city, full_place=full, lat=lat_f, lon=lon_f)


def fetch_open_meteo(lat: float, lon: float) -> dict[str, Any] | None:
    q = urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "timezone": "auto",
            "current": "temperature_2m,apparent_temperature,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "hourly": "precipitation_probability,rain,weather_code",
            "forecast_days": 3,
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{q}"
    try:
        return http_get_json(url)
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        print(f"Weather request failed: {e}", file=sys.stderr)
        return None


def _daily_first(daily: dict[str, Any], key: str) -> Any | None:
    arr = daily.get(key)
    if isinstance(arr, list) and arr:
        return arr[0]
    return None


def _parse_local_time(iso: str, tz_name: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt


def _format_clock_speech(dt: datetime) -> str:
    """Exact local time for openings, e.g. '3:07 PM' — 12-hour, no leading zero on hour."""
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


def _format_time_speech(dt: datetime) -> str:
    """e.g. 'around 5 PM' — 12-hour clock, no leading zero on hour."""
    h24 = dt.hour
    if h24 == 0:
        h12, suf = 12, "AM"
    elif h24 < 12:
        h12, suf = h24, "AM"
    elif h24 == 12:
        h12, suf = 12, "PM"
    else:
        h12, suf = h24 - 12, "PM"
    if dt.minute >= 30:
        return f"around {h12} thirty {suf}"
    if dt.minute > 0:
        return f"around {h12} {suf}"
    return f"around {h12} {suf}"


def _next_meaningful_rain_hint(
    payload: dict[str, Any],
    *,
    now_local: datetime,
    tz_name: str,
) -> str | None:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    probs = hourly.get("precipitation_probability") or []
    rains = hourly.get("rain") or []
    codes = hourly.get("weather_code") or []
    n = min(len(times), len(probs), len(rains), len(codes))
    if n == 0:
        return None
    threshold_prob = 40
    threshold_rain_mm = 0.15
    for i in range(n):
        try:
            t = _parse_local_time(str(times[i]), tz_name)
        except (TypeError, ValueError, OSError):
            continue
        if t <= now_local:
            continue
        if t > now_local + timedelta(hours=36):
            break
        try:
            p = float(probs[i]) if probs[i] is not None else 0.0
            r = float(rains[i]) if rains[i] is not None else 0.0
            code = int(codes[i]) if codes[i] is not None else -1
        except (TypeError, ValueError):
            continue
        wet = code in RAIN_OR_WET_CODES
        if wet and (p >= threshold_prob or r >= threshold_rain_mm):
            return f"You may see {wmo_weather_phrase(code)} starting {_format_time_speech(t)}."
        if r >= 0.3 and p >= 30:
            return f"Rain looks possible around {_format_time_speech(t)}."
    return None


def compose_weather_speech(
    geo: GeoResult,
    payload: dict[str, Any],
    *,
    calendar_phrase: str | None = None,
) -> str:
    """Build one continuous line for macOS ``say``."""
    tz_name = str(payload.get("timezone") or "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except (TypeError, ValueError, OSError):
        print(
            f"[Weather] Unrecognised timezone {tz_name!r} from Open-Meteo; falling back to UTC. "
            "Times in the briefing may be wrong.",
            file=sys.stderr,
            flush=True,
        )
        tz = ZoneInfo("UTC")
    # Wall clock in the forecast timezone — not ``current.time`` from the API, which
    # can lag (e.g. top-of-hour observation) and would read the wrong minutes aloud.
    wall_now = datetime.now(tz)

    current = payload.get("current") or {}
    daily = payload.get("daily") or {}

    t = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    cur_code = current.get("weather_code")
    dmax = _daily_first(daily, "temperature_2m_max")
    dmin = _daily_first(daily, "temperature_2m_min")
    dcode = _daily_first(daily, "weather_code")
    rain_p = _daily_first(daily, "precipitation_probability_max")

    def fmt_temp(x: Any) -> str:
        try:
            return str(int(round(float(x))))
        except (TypeError, ValueError):
            return "unknown"

    city = geo.city
    phrase_now = wmo_weather_phrase(int(cur_code) if cur_code is not None else None)
    phrase_day = wmo_weather_phrase(int(dcode) if dcode is not None else None)

    weekday = wall_now.strftime("%A")
    month = wall_now.strftime("%B")
    day_ord = ordinal_day(wall_now.day)

    time_spoken = _format_clock_speech(wall_now)
    cal_line = (
        calendar_phrase
        if calendar_phrase is not None
        else today_upcoming_calendar_speech()
    )

    opening = (
        f"Welcome back sir. It's {time_spoken}. Today is {weekday} the {day_ord} of {month}. "
        f"{cal_line}"
        f"The current weather in {city} is {phrase_now}, about {fmt_temp(t)} degrees"
    )
    try:
        t_num = float(t) if t is not None else None
        f_num = float(feels) if feels is not None else None
    except (TypeError, ValueError):
        t_num, f_num = None, None
    if t_num is not None and f_num is not None and abs(f_num - t_num) >= 1.5:
        opening += f", feels like about {fmt_temp(feels)}"
    opening += "."

    # Avoid repeating the same condition twice (current + today) when they're identical.
    if phrase_day and phrase_day != phrase_now:
        today_line = f"Today: {phrase_day}. High near {fmt_temp(dmax)}, low near {fmt_temp(dmin)}"
    else:
        today_line = f"For today: High near {fmt_temp(dmax)}, low near {fmt_temp(dmin)}"

    outlook_bits: list[str] = [today_line]
    if rain_p is not None:
        try:
            rp = int(round(float(rain_p)))
            if rp >= 15:
                outlook_bits.append(f"Rain chance up to {rp} percent")
        except (TypeError, ValueError):
            pass
    outlook = ". ".join(outlook_bits) + "."

    rain_hint = _next_meaningful_rain_hint(payload, now_local=wall_now, tz_name=tz_name)
    closing = "Have a nice day!"
    if rain_hint:
        return " ".join([opening, outlook, rain_hint, closing])
    return " ".join([opening, outlook, closing])


def compose_weather_speech_or_error() -> str:
    """Fetch location, forecast, and calendar in parallel where possible."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_cal = pool.submit(today_upcoming_calendar_speech)
        fut_loc = pool.submit(fetch_ip_location)
        loc = fut_loc.result()
        if loc is None:
            wait({fut_cal})
            return "Could not determine your city from the network. Have a nice day!"
        fut_wx = pool.submit(fetch_open_meteo, loc.lat, loc.lon)
        wx = fut_wx.result()
        cal_phrase = fut_cal.result()
    if wx is None:
        return "Sorry sir, I could not fetch the weather right now. Have a nice day!"
    return compose_weather_speech(loc, wx, calendar_phrase=cal_phrase)


def compose_current_weather_brief_speech() -> str:
    """Short spoken current conditions (city + sky + temp); for quick voice Q&A."""
    loc = fetch_ip_location()
    if loc is None:
        return "I couldn't determine your city for weather."
    wx = fetch_open_meteo(loc.lat, loc.lon)
    if wx is None:
        return "I couldn't fetch the weather right now."
    current = wx.get("current") or {}
    t = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    cur_code = current.get("weather_code")

    def fmt_temp(x: Any) -> str:
        try:
            return str(int(round(float(x))))
        except (TypeError, ValueError):
            return "unknown"

    phrase = wmo_weather_phrase(int(cur_code) if cur_code is not None else None)
    line = f"In {loc.city}: {phrase}, about {fmt_temp(t)} degrees"
    try:
        t_num = float(t) if t is not None else None
        f_num = float(feels) if feels is not None else None
    except (TypeError, ValueError):
        t_num, f_num = None, None
    if t_num is not None and f_num is not None and abs(f_num - t_num) >= 1.5:
        line += f", feels like about {fmt_temp(feels)}"
    return line + "."


def spoken_location_brief() -> str:
    """Approximate place from IP (same source as weather); VPNs can skew results."""
    loc = fetch_ip_location()
    if loc is None:
        return "I couldn't determine your location from the network."
    return f"Based on your network address, you're near {loc.full_place}."


def spoken_local_time_now() -> str:
    """Local wall clock + weekday and calendar date for speech."""
    now = datetime.now().astimezone()
    clock = _format_clock_speech(now)
    weekday = now.strftime("%A")
    month = now.strftime("%B")
    day_ord = ordinal_day(now.day)
    return f"It's {clock} on {weekday} the {day_ord} of {month}."
