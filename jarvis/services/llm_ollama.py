"""
Local LLM via Ollama (https://ollama.com).

We keep this dependency-free by using stdlib HTTP. The default Ollama server is
http://localhost:11434 and supports the /api/generate endpoint.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def ollama_generate(
    *,
    host: str,
    model: str,
    prompt: str,
    system: str | None = None,
    temperature: float = 0.4,
    timeout_s: float = 30.0,
) -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        raise ValueError("Ollama host is empty.")
    model = (model or "").strip()
    if not model:
        raise ValueError("Ollama model is empty.")

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": float(temperature)},
    }
    if system:
        payload["system"] = system

    body = json.dumps(payload).encode("utf-8")
    req = Request(
        f"{host}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=float(timeout_s)) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    out = data.get("response")
    if not isinstance(out, str):
        raise ValueError("Unexpected Ollama response shape (missing 'response').")
    return out.strip()


_ABBREVS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "rev", "gen", "sgt",
    "cpl", "pvt", "st", "vs", "etc", "inc", "ltd", "dept", "fig", "vol",
    "est", "approx", "govt",
})
_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'\(])")


def _split_sentence(buf: str) -> tuple[str, str]:
    """
    Find the first real sentence boundary in buf.
    Returns (sentence, remainder). Returns ("", buf) if no boundary found.
    Skips abbreviations (Dr., Mr., etc.) and single-letter initials (J.).
    """
    for m in _SPLIT_RE.finditer(buf):
        preceding = buf[: m.start()]
        # Extract the word immediately before the terminal punctuation.
        word_m = re.search(r"\b([A-Za-z]+)[.!?]$", preceding)
        if word_m:
            w = word_m.group(1).lower()
            if w in _ABBREVS or len(w) == 1:
                continue
        return preceding.strip(), buf[m.end():]
    return "", buf


def ollama_generate_stream(
    *,
    host: str,
    model: str,
    prompt: str,
    system: str | None = None,
    temperature: float = 0.4,
    timeout_s: float = 30.0,
    max_sentences: int | None = None,
) -> Iterator[str]:
    """Stream from Ollama (stream=True), yielding complete sentences as they arrive."""
    host = (host or "").strip().rstrip("/")
    if not host:
        raise ValueError("Ollama host is empty.")
    model = (model or "").strip()
    if not model:
        raise ValueError("Ollama model is empty.")

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": float(temperature)},
    }
    if system:
        payload["system"] = system

    body = json.dumps(payload).encode("utf-8")
    req = Request(
        f"{host}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    buffer = ""
    yielded = 0
    max_s = None if max_sentences is None else max(1, int(max_sentences))
    with urlopen(req, timeout=float(timeout_s)) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            buffer += data.get("response", "")
            while True:
                sentence, buffer = _split_sentence(buffer)
                if not sentence:
                    break
                yield sentence
                yielded += 1
                if max_s is not None and yielded >= max_s:
                    return
            if data.get("done"):
                break
    if buffer.strip():
        if max_s is None or yielded < max_s:
            yield buffer.strip()


def extract_spoken_clock_from_source_briefing(source: str) -> str | None:
    """Parse ``It's … . Today is`` from ``compose_weather_speech`` opening; return clock phrase only."""
    m = re.search(r"(?i)\bit's\s+(.+?)\.\s+today\s+is\b", (source or "").strip())
    if not m:
        return None
    clock = m.group(1).strip()
    return clock or None


def inject_spoken_clock_after_greeting(rewritten: str, source: str) -> str:
    """If the model dropped the wall clock, put ``it's {clock}`` right after the greeting."""
    text = (rewritten or "").strip()
    if not text:
        return rewritten
    clock = extract_spoken_clock_from_source_briefing(source)
    if not clock:
        return rewritten
    if clock.lower() in text.lower():
        return rewritten

    def _needs_clock_prefix(s: str) -> bool:
        sl = s.lower().lstrip()
        return not (sl.startswith("it's ") or sl.startswith("it is "))

    # "Greeting clause, …" → insert clock after the first comma (model should use this shape).
    m = re.match(r"^([^,]{2,160}),\s*(.+)$", text, flags=re.DOTALL)
    if m:
        greet, rest = m.group(1).strip(), m.group(2).strip()
        if _needs_clock_prefix(rest):
            return f"{greet}, it's {clock}, {rest}".strip()

    # "… sir …" without an early comma (rare) → insert after the first "sir".
    m2 = re.match(r"(?i)^(.+?\bsir)\s+(.+)$", text)
    if m2:
        prefix, tail = m2.group(1).strip(), m2.group(2).strip()
        if _needs_clock_prefix(tail):
            return f"{prefix}, it's {clock}, {tail}".strip()

    return f"Welcome back sir, it's {clock}. {text}".strip()


def rewrite_weather_briefing_with_ollama(
    weather_text: str,
    *,
    host: str,
    model: str,
    timeout_s: float = 30.0,
) -> str:
    """
    Rewrite the full briefing (calendar + weather) for natural TTS, with fixed section order.
    On any error, returns the original text.
    """
    text = (weather_text or "").strip()
    if not text:
        return weather_text

    system = (
        "You are Jarvis, a calm personal assistant rewriting a spoken briefing for text-to-speech.\n\n"
        "STRICT ORDER — one continuous paragraph, in this exact sequence:\n"
        "1) GREETING: One short clause you choose (vary wording across sessions). Tone: professional, warm, "
        "as if the user has just returned to their desk or work session — e.g. welcome back, good to have you back, "
        "ready when you are, shall we begin — address them as 'sir' when it fits naturally. "
        "Do not default to the exact phrase 'Hello sir.' End this clause with a comma, then continue.\n"
        "2) SCHEDULE (calendar block — before any weather): First, if the source mentions the current clock time, "
        "weekday, or calendar date (e.g. 'Thursday', 'April', 'the 17th', '8:08 PM'), you MUST include them here "
        "in natural speech, once, before listing events. Never omit time, weekday, or date that appear in the source.\n"
        "   Then list all calendar events with their times and titles. If there are no events in the source, say that "
        "briefly after the time/date line.\n"
        "3) WEATHER: All weather-only content — city, conditions, temperatures, highs/lows, rain chance, rain-soon hints. "
        "Every weather fact appears only in this block; do not mention weather in section 2.\n"
        "4) CLOSING: One short positive line (e.g. 'Have a nice day.').\n\n"
        "RULES:\n"
        "- Do not repeat the same information twice (especially weather).\n"
        "- Do not interleave weather and schedule; finish section 2 before starting section 3.\n"
        "- Preserve every factual detail from the source. Do not invent facts.\n"
        "Return only the final briefing text, no labels, bullets, or meta-commentary."
    )
    prompt = (
        f"Source briefing (may mix calendar and weather):\n{text}\n\n"
        "Rewrite: your own short welcome-back-to-work style greeting ending with a comma → "
        "then time/weekday/date from source (if any) and all schedule items → "
        "then all weather → then closing. Do not drop time, weekday, or date if they are in the source."
    )
    try:
        out = ollama_generate(
            host=host,
            model=model,
            prompt=prompt,
            system=system,
            temperature=0.28,
            timeout_s=timeout_s,
        )
        if not out:
            return weather_text
        return inject_spoken_clock_after_greeting(out, text)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as e:
        print(f"[LLM] ollama rewrite failed: {e}", file=sys.stderr, flush=True)
        return weather_text
