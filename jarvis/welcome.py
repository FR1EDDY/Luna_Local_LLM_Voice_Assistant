"""Orchestration after a double clap: Spotify, Notion, optional vocals, weather + TTS."""

from __future__ import annotations

import re
import time
from datetime import date
from pathlib import Path

from jarvis.config import BRIEFING_HEADLINE_COUNT
from jarvis.integrations import mac_audio, notion, spotify
from jarvis.integrations.macos_apps import open_app
from jarvis.services import weather
from jarvis.services.calendar import daily_briefing_schedule_md_lines

# Spoken closing removed before appending news so the sign-off comes last.
_CLOSING_TAIL_RE = re.compile(
    r"(?i)\s*[.;,]?\s*have a (?:nice|great|good) day[!.]*\s*$",
)


def _strip_trailing_briefing_closing(text: str) -> str:
    t = _CLOSING_TAIL_RE.sub("", text.rstrip()).rstrip()
    return t.rstrip(",").rstrip(";")


def _build_daily_briefing_md(
    today: date,
    raw_headlines: list[str],
) -> str:
    """Build a structured markdown daily briefing with metadata frontmatter."""
    from jarvis.services.plans import wrap_markdown

    lines: list[str] = [
        f"# Daily Briefing — {today.strftime('%A, %B %-d %Y')}",
        "",
    ]

    lines.append("## Schedule")
    try:
        lines.extend(daily_briefing_schedule_md_lines(today))
    except Exception:
        lines.append("- Calendar unavailable")

    lines.append("")

    lines.append("## Business & Markets News")
    if raw_headlines:
        for h in raw_headlines:
            parts = h.split(" — ", 1)
            title = parts[0].strip()
            source = parts[1].strip() if len(parts) > 1 else ""
            if source:
                lines.append(f"- {title} *({source})*")
            else:
                lines.append(f"- {title}")
    else:
        lines.append("- No headlines fetched")

    lines.append("")
    body = "\n".join(lines)
    return wrap_markdown(body, date_str=today.isoformat())


def run_welcome_sequence(
    *,
    spotify_uri: str,
    vocals_path: Path,
    no_vocals: bool,
    no_weather: bool,
    tts_backend: str,
    llm_backend: str,
    ollama_host: str | None,
    ollama_model: str | None,
    tts_duck_volume: int | None,
    spotify_activate: bool,
    weather_output_volume: float,
    open_notion_after_spotify: bool,
    notion_app: str,
    notion_fullscreen: bool,
    pre_announcement_delay_s: float,
) -> list[str]:
    """Run the welcome sequence and return raw headline strings (may be empty)."""
    weather_text: str | None = None
    raw_headlines: list[str] = []
    today = date.today()

    # Start Spotify/Notion first so playback is not blocked by network + calendar I/O.
    spotify.play_spotify_uri(spotify_uri, activate=spotify_activate)

    if open_notion_after_spotify:
        notion.open_notion(app_name=notion_app, try_fullscreen=notion_fullscreen)

    if not no_weather:
        weather_text = weather.compose_weather_speech_or_error()

        # Fetch headlines separately — do NOT pass through Ollama rewrite so they aren't dropped.
        try:
            from jarvis.services.news import fetch_headlines
            raw_headlines = fetch_headlines(max_items=BRIEFING_HEADLINE_COUNT)
        except Exception:
            raw_headlines = []

        if weather_text is not None:
            from jarvis.services.llm import rewrite_weather_if_enabled

            weather_text = rewrite_weather_if_enabled(
                weather_text,
                llm_backend=llm_backend,
                ollama_host=ollama_host,
                ollama_model=ollama_model,
            )

        # Append headlines after weather (after the rewrite so Ollama can't drop them).
        # Move the briefing closing after news so we don't say "have a nice day" before headlines.
        if weather_text and raw_headlines:
            titles = [h.split(" — ")[0].strip() for h in raw_headlines]
            if len(titles) == 1:
                news_speech = f"In the news today: {titles[0]}."
            else:
                news_speech = "Top business stories today: " + ". ".join(titles) + "."
            news_speech += " Call my name if you'd like to discuss any of these."
            core = _strip_trailing_briefing_closing(weather_text)
            core = core.rstrip().rstrip(".")
            weather_text = f"{core}. {news_speech} Have a nice day."

        print(weather_text, flush=True)

    # Save daily briefing markdown (best-effort, non-blocking).
    try:
        from jarvis.paths import PROJECT_ROOT
        briefing_dir = PROJECT_ROOT / "plans"
        briefing_dir.mkdir(exist_ok=True)
        briefing_path = briefing_dir / f"{today.isoformat()}.md"
        briefing_path.write_text(_build_daily_briefing_md(today, raw_headlines), encoding="utf-8")
        print(f"[Welcome] Daily briefing saved: {briefing_path.name}", flush=True)
    except Exception as ex:
        print(f"[Welcome] Could not save briefing: {ex}", flush=True)

    if not no_vocals:
        if weather_text is not None:
            mac_audio.play_vocals_wav_blocking(vocals_path)
        else:
            mac_audio.play_vocals_wav(vocals_path)

    if weather_text is not None:
        from jarvis.services.tts import speak_weather

        delay = max(0.0, min(120.0, float(pre_announcement_delay_s)))
        if delay > 0.0:
            time.sleep(delay)

        speak_weather(
            weather_text,
            tts_backend=tts_backend,
            output_volume=weather_output_volume,
            spotify_duck_volume=tts_duck_volume,
            spotify_duck_halve=tts_duck_volume is None,
        )

    return raw_headlines
