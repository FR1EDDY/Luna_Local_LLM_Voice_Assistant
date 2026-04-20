"""After the LUNA wake word: multi-turn voice dialogue (Vosk/Whisper → Ollama → TTS), until goodbye or silence."""

from __future__ import annotations

import json
import os
import queue as _queue
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError

import numpy as np

from jarvis import config
from jarvis.audio import rms as _rms
from jarvis.integrations import calendar_events, cursor_mode, mac_audio
from jarvis.luna.commands import (
    try_parse_add_event_missing_date,
    try_parse_add_event_partial,
    try_voice_command,
)
from jarvis.luna.stt_normalize import normalize_command_transcript
from jarvis.services.llm_ollama import ollama_generate, ollama_generate_stream
from jarvis.services.tts import speak_weather

_MEMORY_PATH = Path.home() / ".jarvis_memory.json"
_MEMORY_MAX_TURNS = 20

_EXPLICIT_WEB_SEARCH_RE = re.compile(
    r"^\s*(?:web\s+)?(?:search|look\s+up|google|browse)\s+(?:for\s+)?(.+?)\s*$",
    re.IGNORECASE,
)

_ELABORATE_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|sure|ok|okay)?\s*(?:please\s+)?"
    r"(?:elaborate|expand|go\s+deeper|more\s+detail|more\s+details|in\s+detail|detailed|full\s+explanation)\s*$",
    re.IGNORECASE,
)

_PLAN_RE = re.compile(
    r"\b(?:create|make|write|generate|build|draft)\s+(?:a\s+)?(?:detailed\s+)?(?:plan|roadmap|outline|checklist|guide|blueprint)\s+(?:for|to|about|on|called|named)\s+(.+)",
    re.IGNORECASE,
)



def _apply_luna_mic_gain(mono: np.ndarray, gain: float) -> np.ndarray:
    """Boost quiet input for speech detection and transcription; clip to [-1, 1]."""
    if gain == 1.0:
        return np.asarray(mono, dtype=np.float32).reshape(-1)
    x = np.asarray(mono, dtype=np.float64).reshape(-1) * float(gain)
    return np.clip(x, -1.0, 1.0).astype(np.float32)


def _normalize_utterance(text: str) -> str:
    return re.sub(r"[^\w\s]", "", (text or "").lower()).strip()


_END_SESSION_RE = re.compile(config.LUNA_CHAT_END_SESSION_PATTERN, re.IGNORECASE)

# Whole-utterance “no thanks” style endings (normalized, single line).
_BARE_END_UTTERANCES = frozenset(
    {
        "no",
        "nope",
        "nah",
        "no thanks",
        "no thank you",
        "not now",
        "thats all",
        "that's all",
    }
)


def _should_end_session(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _normalize_utterance(t) in _BARE_END_UTTERANCES:
        return True
    return bool(_END_SESSION_RE.search(t))


# Cancel current turn / session without goodbye TTS (whole utterance, normalized).
_ABORT_LISTENING_UTTERANCES = frozenset(
    {
        "stop",
        "cancel",
        "never mind",
        "nevermind",
        "forget it",
        "forget that",
        "skip",
        "skip it",
        "abort",
        "quiet",
        "enough",
    }
)


def _is_abort_listening_command(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return _normalize_utterance(t) in _ABORT_LISTENING_UTTERANCES


def _extract_time_token(text: str) -> str | None:
    """
    Best-effort extraction of a time token from a short follow-up utterance.
    Supports cases like: "five pm" (already normalized by Vosk), "5 pm",
    "5:15 pm", "17 30", "at 5pm", etc.
    """
    raw = (text or "").strip().lower()
    if not raw:
        return None

    # If the entire utterance parses as a time, accept it.
    if calendar_events.parse_time_token(raw) is not None:
        return raw

    # Try to pull a likely time substring and validate it via the parser.
    candidates = [
        # 5 pm, 5:15 pm, 12am, 12:00am
        r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm)\b",
        # 17:30, 7:05
        r"\b\d{1,2}:\d{2}\b",
        # 1730 / 0705 (compact) - only if it looks like HHMM.
        r"\b\d{3,4}\b",
    ]
    for pat in candidates:
        m = re.search(pat, raw)
        if not m:
            continue
        tok = m.group(0).replace(".", "").strip()
        if calendar_events.parse_time_token(tok) is not None:
            return tok

    return None


class LunaVoiceFollowup:
    """
    State machine driven from the sounddevice callback (single audio thread).
    ``idle`` only when no dialogue session is active.
    """

    def __init__(
        self,
        *,
        greeting_fn: Callable[[], None],
        sample_rate: int,
        vosk_model_dir: Path,
        ollama_host: str,
        ollama_model: str,
        tts_backend: str,
        output_volume: float,
        spotify_duck_halve: bool = True,
        idle_reset_fn: Callable[[], None] | None = None,
        notion_app: str = "Notion",
        notion_fullscreen: bool = False,
        ui_state_callback: Callable[[str], None] | None = None,
        ui_text_callback: Callable[[str, str], None] | None = None,
        voice_rms_threshold: float | None = None,
        mic_gain: float | None = None,
        transcribe_backend: str = "whisper",
        ui_token_callback: "Callable[[str], None] | None" = None,
        ui_level_callback: "Callable[[float], None] | None" = None,
        ui_plan_callback: "Callable[[str, str], None] | None" = None,
    ) -> None:
        self._ui_state_callback = ui_state_callback
        self._ui_text_callback = ui_text_callback
        self._ui_token_callback = ui_token_callback
        self._ui_plan_callback = ui_plan_callback
        self._idle_reset_fn = idle_reset_fn
        self._notion_app = (notion_app or "Notion").strip()
        self._notion_fullscreen = bool(notion_fullscreen)
        self._greeting_fn = greeting_fn
        self._sr = int(sample_rate)
        self._vosk_dir = vosk_model_dir.expanduser().resolve()
        self._ollama_host = (ollama_host or "").strip().rstrip("/")
        self._ollama_model = (ollama_model or "").strip()
        self._tts_backend = (tts_backend or "kokoro").strip().lower()
        # Cap incoming gain at 1.0 (max usable amplitude with the live-volume sounddevice
        # path; values above 1.0 just clip). Legacy CLI defaults like 1.35 collapse to 1.0.
        self._vol = max(0.0, min(1.0, float(output_volume)))
        self._spotify_duck_halve = bool(spotify_duck_halve)
        self._transcribe_backend = (transcribe_backend or "vosk").strip().lower()
        self._model: Any = None
        self._whisper_model: Any = None
        self._lock = threading.Lock()
        self._phase = "idle"
        self._set_ui_state("idle")
        self._gap_until = 0.0
        self._chunks: list[np.ndarray] = []
        self._had_speech = False
        self._last_voice_mono = 0.0
        self._collect_started_mono = 0.0
        self._history: list[tuple[str, str]] = []
        self._completed_turns = 0
        self._session_generation = 0
        # (title, date_token, start_time_token, end_time_token)
        self._pending_calendar_add: tuple[str | None, str | None, str | None, str | None] | None = None
        # Topic name waiting for overwrite confirmation (set when plan file already exists)
        self._pending_plan_overwrite: str | None = None
        self._text_session: bool = False
        self._news_context: list[str] = []
        self._last_user_question: str | None = None
        self._last_search_ctx: str | None = None
        self._voice_rms_thr = (
            float(voice_rms_threshold)
            if voice_rms_threshold is not None
            else float(config.LUNA_CMD_VOICE_RMS)
        )
        self._mic_gain = config.clamp_luna_mic_gain(
            float(mic_gain) if mic_gain is not None else float(config.LUNA_MIC_GAIN)
        )
        self._whisper_cpu_threads = max(1, min(8, (os.cpu_count() or 4)))

        # Best-effort: warm up Whisper early so first command is faster.
        self._preload_whisper_if_enabled()

    # Perceptual curve so the 0–100 slider feels "linear in loudness".
    # Raw amplitude gain is linear, but human hearing is roughly logarithmic — without a
    # curve, a slider at 30 sounds about half as loud and a slider at 3 is still audible.
    # ``vol = (p/100)**2`` maps 100→1.0 (max, no clipping), 50→0.25 (~half loudness),
    # 10→0.01, so each slider step has the perceived weight people expect.
    @staticmethod
    def _percent_to_vol(p: int) -> float:
        p = max(0, min(100, int(p)))
        return (p / 100.0) ** 2

    @staticmethod
    def _vol_to_percent(v: float) -> int:
        v = max(0.0, min(1.0, float(v)))
        return max(0, min(100, int(round((v ** 0.5) * 100))))

    def get_voice_volume_percent(self) -> int:
        """Slider position 0–100 (perceptual); inverse of ``set_voice_volume_percent``."""
        return self._vol_to_percent(self._vol)

    def set_voice_volume_percent(self, percent: int) -> int:
        """Clamp to 0–100, apply perceptual curve, and update the live gain mid-speech."""
        p = max(0, min(100, int(percent)))
        self._vol = self._percent_to_vol(p)
        try:
            from jarvis.services.live_audio import set_live_gain

            set_live_gain(self._vol)
        except Exception:
            pass
        return p

    def _decide_event_duration_minutes(self, title: str) -> int:
        """
        Use Ollama to pick a sensible duration when no end time is provided.
        Returns minutes (15–240). Falls back to heuristics if Ollama fails.
        """
        t = (title or "").strip().lower()
        fallback = 90 if "work" in t else 60
        try:
            reply = ollama_generate(
                host=self._ollama_host,
                model=self._ollama_model,
                system=(
                    "You decide a calendar event duration in minutes.\n"
                    "Return ONLY an integer number of minutes between 15 and 240.\n"
                    "Use common sense defaults: work block ~90, short task 30, meeting 60, lecture 90.\n"
                    "No words, no punctuation."
                ),
                prompt=f"Event title: {title}",
                temperature=0.0,
                timeout_s=8.0,
            )
            m = re.search(r"\d+", (reply or ""))
            if not m:
                return fallback
            v = int(m.group(0))
            return max(15, min(240, v))
        except Exception:
            return fallback

    @property
    def idle(self) -> bool:
        with self._lock:
            return self._phase == "idle"

    def _set_ui_state(self, state: str) -> None:
        if self._ui_state_callback:
            try:
                self._ui_state_callback(state)
            except Exception:
                pass

    def _invoke_idle_reset(self) -> None:
        if self._idle_reset_fn is not None:
            try:
                self._idle_reset_fn()
            except Exception:  # noqa: BLE001
                pass

    def set_news_context(self, headlines: list[str]) -> None:
        """Call after a briefing to prime the next chat session with today's headlines."""
        with self._lock:
            self._news_context = list(headlines)

    def _inject_news_context(self) -> None:
        """Inject stored headlines as a priming turn so Luna can discuss them. Call inside lock."""
        if not self._news_context:
            return
        joined = ". ".join(self._news_context)
        self._history.append((
            "What are today's top news headlines?",
            f"Here are today's top stories: {joined}. Let me know if you'd like to discuss any of them.",
        ))
        self._news_context = []

    def start_after_wake(self, now: float) -> None:
        with self._lock:
            if self._phase != "idle":
                return
            self._session_generation += 1
            self._history = self._load_long_term_memory()
            self._inject_news_context()
            self._completed_turns = 0
            self._phase = "gap"
            self._gap_until = now + float(config.LUNA_POST_WAKE_GAP_S)
        self._set_ui_state("waking")
        self._sync_live_output_gain()
        threading.Thread(target=self._safe_greeting, daemon=True).start()

    def barge_in_luna(self, now: float) -> None:
        """Say the wake word again while a session is active: stop TTS and restart from the greeting."""
        with self._lock:
            if self._phase in ("idle", "collecting"):
                # Don't interrupt the user while they're actively speaking — they may have prefixed
                # their command with "Luna" (e.g. "Luna, tell me about X"), which would otherwise
                # immediately restart the session before they finish talking.
                return
            self._session_generation += 1
            self._chunks = []
            self._history = self._load_long_term_memory()
            self._completed_turns = 0
            self._pending_calendar_add = None
            self._pending_plan_overwrite = None
            self._phase = "gap"
            self._gap_until = now + float(config.LUNA_POST_WAKE_GAP_S)
        mac_audio.stop_spoken_output_macos()
        self._sync_live_output_gain()
        self._set_ui_state("waking")
        threading.Thread(target=self._safe_greeting, daemon=True).start()

    def force_stop(self) -> None:
        """Immediately kill TTS playback and return to idle — used by the UI stop button."""
        mac_audio.stop_spoken_output_macos()
        self._sync_live_output_gain()
        with self._lock:
            self._session_generation += 1
            self._chunks = []
            self._history.clear()
            self._completed_turns = 0
            self._pending_calendar_add = None
            self._pending_plan_overwrite = None
            self._phase = "idle"
        self._set_ui_state("idle")
        self._invoke_idle_reset()

    def _sync_live_output_gain(self) -> None:
        """Re-apply UI/output volume to the shared live-gain path (fixes silence after barge-in/stop)."""
        try:
            from jarvis.services.live_audio import set_live_gain

            set_live_gain(float(self._vol))
        except Exception:
            pass

    def process_text_input(self, text: str) -> None:
        """Process a message typed in the UI text chat (no STT, same command/LLM pipeline)."""
        text = text.strip()
        if not text:
            return
        with self._lock:
            if self._phase == "processing":
                return
            if self._phase == "idle":
                self._history = self._load_long_term_memory()
                self._completed_turns = 0
            self._session_generation += 1
            self._phase = "processing"
            self._text_session = True
        self._set_ui_state("thinking")
        if self._ui_text_callback:
            try:
                self._ui_text_callback(text, "")
            except Exception:
                pass
        threading.Thread(
            target=self._pipeline,
            args=(np.array([], dtype=np.float32),),
            kwargs={"pre_transcribed": text},
            daemon=True,
        ).start()

    def _safe_greeting(self) -> None:
        try:
            self._greeting_fn()
        except Exception as ex:  # noqa: BLE001
            print(f"[Luna] greeting playback error: {ex}", file=sys.stderr, flush=True)

    def _pre_speech_timeout(self) -> float:
        if self._completed_turns == 0:
            return float(config.LUNA_CMD_PRE_SPEECH_TIMEOUT_S)
        return float(config.LUNA_CHAT_FOLLOWUP_PRE_SPEECH_TIMEOUT_S)

    def feed_audio_block(self, mono: np.ndarray, now: float) -> None:
        with self._lock:
            phase = self._phase
            if phase == "idle" or phase == "processing":
                return

            if phase in ("post_tts_gap", "gap"):
                if now >= self._gap_until:
                    self._phase = "collecting"
                    self._chunks = []
                    self._had_speech = False
                    self._collect_started_mono = now
                    self._last_voice_mono = now
                    print("[Luna] Listening…", flush=True)
                    self._set_ui_state("listening")
                return

            if phase != "collecting":
                return

        boosted = _apply_luna_mic_gain(mono, self._mic_gain)
        self._chunks.append(boosted.copy())
        level = _rms(boosted)
        thr = float(self._voice_rms_thr)
        if level >= thr:
            self._had_speech = True
            self._last_voice_mono = now

        pre_to = self._pre_speech_timeout()
        if not self._had_speech and (now - self._collect_started_mono) >= pre_to:
            if self._completed_turns == 0:
                self._abort_no_speech_first()
            else:
                self._silence_end_session()
            return

        if self._had_speech:
            if (now - self._last_voice_mono) >= float(config.LUNA_CMD_END_SILENCE_S):
                self._finalize_and_process()
                return
            if (now - self._collect_started_mono) >= float(config.LUNA_CMD_MAX_DURATION_S):
                self._finalize_and_process()
                return

    def _abort_no_speech_first(self) -> None:
        with self._lock:
            self._chunks = []
            self._phase = "idle"
            self._history.clear()
            self._completed_turns = 0
            self._pending_calendar_add = None
            self._pending_plan_overwrite = None
        self._set_ui_state("idle")
        print("[Luna] (no speech after the chime — session ended)", flush=True)
        self._invoke_idle_reset()

    def _silence_end_session(self) -> None:
        with self._lock:
            self._chunks = []
            self._phase = "idle"
            history_snapshot = list(self._history)
            self._history.clear()
            self._completed_turns = 0
            self._pending_calendar_add = None
            self._pending_plan_overwrite = None
        self._set_ui_state("idle")
        print("[Luna] (no reply — session ended)", flush=True)
        if history_snapshot:
            self._save_long_term_memory(history_snapshot)
        self._invoke_idle_reset()

    def _release_mic_for_external_dictation(self) -> None:
        """Stop capturing audio so macOS dictation (or Cursor) can use the microphone."""
        with self._lock:
            self._chunks = []
            self._phase = "idle"
            history_snapshot = list(self._history)
            self._history.clear()
            self._completed_turns = 0
            self._pending_calendar_add = None
            self._pending_plan_overwrite = None
        self._set_ui_state("idle")
        print("[Luna] Mic released for macOS dictation (double-clap + wake word when finished).", flush=True)
        if history_snapshot:
            self._save_long_term_memory(history_snapshot)
        self._invoke_idle_reset()

    def _finalize_and_process(self) -> None:
        with self._lock:
            parts = self._chunks
            self._chunks = []
            self._phase = "processing"
        self._set_ui_state("thinking")
        if not parts:
            with self._lock:
                self._phase = "idle"
            self._set_ui_state("idle")
            return
        wave = np.concatenate(parts)
        threading.Thread(target=self._pipeline, args=(wave,), daemon=True).start()

    def _begin_followup_listening(self) -> None:
        with self._lock:
            if self._text_session:
                self._phase = "idle"
                self._text_session = False
            else:
                self._phase = "post_tts_gap"
                self._gap_until = time.monotonic() + float(config.LUNA_POST_TTS_GAP_S)
        if self._phase == "idle":
            self._set_ui_state("idle")
        else:
            self._set_ui_state("listening")

    # ------------------------------------------------------------------ memory
    def _load_long_term_memory(self) -> list[tuple[str, str]]:
        try:
            data = json.loads(_MEMORY_PATH.read_text())
            turns = data.get("turns", [])
            return [(t["user"], t["luna"]) for t in turns[-_MEMORY_MAX_TURNS:]]
        except Exception:
            return []

    def _save_long_term_memory(self, history: list[tuple[str, str]]) -> None:
        try:
            existing: dict = json.loads(_MEMORY_PATH.read_text()) if _MEMORY_PATH.exists() else {"turns": []}
        except Exception:
            existing = {"turns": []}
        ts = time.time()
        turns: list[dict] = existing.get("turns", [])
        for user, luna in history:
            turns.append({"user": user, "luna": luna, "ts": ts})
        turns = turns[-_MEMORY_MAX_TURNS:]
        try:
            _MEMORY_PATH.write_text(json.dumps({"turns": turns}, indent=2))
        except Exception as e:
            print(f"[Luna] Could not save memory: {e}", file=sys.stderr, flush=True)

    # ------------------------------------------------------------------ plans
    def _generate_and_save_plan(
        self, topic: str, my_gen: int, *, overwrite: bool = False, version: bool = False
    ) -> str:
        """
        Generate a structured markdown plan via Ollama, format it, save to MARKDOWN_OUTPUT_DIR,
        and speak the filename aloud.
        Pass overwrite=True to replace an existing file, version=True to save a new version.
        """
        from datetime import date as _date
        from jarvis.services.plans import (
            build_plan_content, plan_path, save_plan_to, versioned_plan_path,
        )

        date_str = _date.today().isoformat()

        plan_system = (
            "You are a professional project planner. Output ONLY the markdown document — "
            "no preamble, no explanation outside it. Use this EXACT template structure:\n\n"
            "# {TITLE}\n"
            "> One-line description of what this project achieves.\n\n"
            "## Goal\n"
            "A clear 1-2 sentence project goal.\n\n"
            "## Tasks\n"
            "- [ ] Specific actionable task (priority: high)\n"
            "- [ ] Specific actionable task (priority: medium)\n"
            "- [ ] Specific actionable task (priority: low)\n\n"
            "## Notes\n"
            "Important assumptions, constraints, or context.\n\n"
            "## Resources\n"
            "- Relevant resource or reference\n\n"
            "Rules: use ONLY # ## ### headings; each task must include (priority: high/medium/low); "
            "include 5-10 realistic tasks; no extra sections beyond Goal/Tasks/Notes/Resources."
        )
        plan_prompt = f"Create a project plan for: {topic}"

        print(f"[Luna] Generating plan for: {topic!r}", flush=True)
        try:
            from jarvis.services.llm_ollama import ollama_generate
            raw_content = ollama_generate(
                host=self._ollama_host,
                model=self._ollama_model,
                system=plan_system,
                prompt=plan_prompt,
                temperature=0.7,
                timeout_s=60.0,
            )
        except Exception as ex:
            print(f"[Luna] Plan generation failed: {ex}", file=sys.stderr, flush=True)
            self._speak("Sorry, I couldn't generate that plan.")
            return ""
        if not raw_content or my_gen != self._session_generation:
            return ""

        # Apply canonical formatting: metadata frontmatter + TOC + template enforcement
        formatted = build_plan_content(topic, raw_content, date_str=date_str)

        # Determine save path
        if version:
            save_path = versioned_plan_path(topic)
        else:
            save_path = plan_path(topic)

        saved_path = save_plan_to(save_path, formatted)
        print(f"[Luna] Plan saved: {saved_path}", flush=True)

        if self._ui_plan_callback:
            try:
                self._ui_plan_callback(topic, formatted)
            except Exception:
                pass

        filename = saved_path.name
        spoken = (
            f"Created project plan: {filename}. "
            f"I've saved it to your notes folder."
        )
        self._speak(spoken)
        return spoken

    # ------------------------------------------------------------------ web search
    def _explicit_web_search_query(self, text: str) -> str | None:
        """
        Only allow web access when the user explicitly asks.
        Examples:
        - "search for the latest iPhone"
        - "web search tesla stock price"
        - "look up who won the game"
        """
        m = _EXPLICIT_WEB_SEARCH_RE.match((text or "").strip())
        if not m:
            return None
        q = (m.group(1) or "").strip().strip("\"'").strip()
        if not q:
            return None
        # Avoid accidental searches on very short queries like "search" / "google".
        if len(q) < 3:
            return None
        return q

    @staticmethod
    def _is_ambiguous_user_text(text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return True
        # Very short fragments are often STT misses (especially right after wake).
        words = [w for w in re.split(r"\s+", t) if w]
        if len(words) <= 1 and len(t) <= 12:
            return True
        # Common “garbage” fragments that indicate uncertainty.
        if t.lower() in {"um", "uh", "huh", "what", "hello", "hey"}:
            return True
        return False

    def _maybe_handle_elaboration(self, user_text: str, my_gen: int) -> bool:
        """
        If the user asks for more detail, re-ask the last question in detailed mode.
        Returns True if handled.
        """
        if not _ELABORATE_RE.match((user_text or "").strip()):
            return False
        q = (self._last_user_question or "").strip()
        if not q:
            self._speak("Sure. What should I elaborate on?")
            self._begin_followup_listening()
            return True
        prompt = self._build_prompt(q, search_context=self._last_search_ctx, verbosity="detailed")
        system = self._chat_system(verbosity="detailed")
        reply = self._stream_and_speak(prompt, system, my_gen, max_sentences=None)
        if my_gen != self._session_generation:
            return True
        if reply:
            with self._lock:
                if my_gen == self._session_generation:
                    self._history.append((q, reply))
                    self._trim_history()
                    self._completed_turns += 1
            if self._ui_text_callback:
                try:
                    self._ui_text_callback(q, reply)
                except Exception:
                    pass
        self._begin_followup_listening()
        return True

    def _search_ddg(self, query: str) -> str | None:
        """
        Web search pipeline:
        1. DuckDuckGo instant answers (fast, great for facts/entities).
        2. DDG HTML search → Jina AI Reader for top result (real web content, no key).
        """
        from urllib.parse import urlencode
        from urllib.request import Request as _Req, urlopen as _open

        # --- Step 1: instant answer ---
        try:
            params = urlencode({"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"})
            req = _Req(f"https://api.duckduckgo.com/?{params}", headers={"User-Agent": "Mozilla/5.0"})
            with _open(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            abstract = (data.get("AbstractText") or "").strip()
            if abstract and len(abstract) > 40:
                return abstract
        except Exception:
            pass

        # --- Step 2: real search → Jina Reader ---
        return self._search_full(query)

    def _search_full(self, query: str) -> str | None:
        """DDG HTML search → extract top URLs → fetch via Jina Reader (free, no key)."""
        try:
            from urllib.parse import urlencode, unquote
            from urllib.request import Request as _Req, urlopen as _open

            params = urlencode({"q": query, "ia": "web"})
            req = _Req(
                f"https://html.duckduckgo.com/html/?{params}",
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            )
            with _open(req, timeout=7.0) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            urls: list[str] = []
            for m in re.finditer(r"uddg=([^&\"]+)", html):
                url = unquote(m.group(1))
                if url.startswith("http") and "duckduckgo.com" not in url:
                    urls.append(url)
                if len(urls) >= 3:
                    break

            for url in urls:
                result = self._fetch_via_jina(url)
                if result:
                    return result
        except Exception:
            pass
        return None

    def _fetch_via_jina(self, url: str) -> str | None:
        """Fetch any URL as clean readable text via Jina AI Reader (free, no key needed)."""
        try:
            from urllib.request import Request as _Req, urlopen as _open
            req = _Req(
                f"https://r.jina.ai/{url}",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain", "X-No-Cache": "true"},
            )
            with _open(req, timeout=9.0) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            text = text.strip()
            if len(text) < 100:
                return None
            return text[:4000]
        except Exception:
            return None

    # ------------------------------------------------------------------ transcription
    def _ensure_vosk_model(self) -> None:
        if self._model is not None:
            return
        from vosk import Model

        self._model = Model(str(self._vosk_dir))

    def _transcribe_vosk(self, wave: np.ndarray) -> str:
        from vosk import KaldiRecognizer

        self._ensure_vosk_model()
        rec = KaldiRecognizer(self._model, self._sr)
        step = max(1, int(self._sr * 0.02))
        w = np.asarray(wave, dtype=np.float32).reshape(-1)
        for i in range(0, w.size, step):
            chunk = w[i : i + step]
            pcm = np.clip(chunk.astype(np.float64) * 32767.0, -32768, 32767).astype(np.int16).tobytes()
            rec.AcceptWaveform(pcm)
        try:
            tail = json.loads(rec.FinalResult())
        except (json.JSONDecodeError, ValueError):
            return ""
        if not isinstance(tail, dict):
            return ""
        return (tail.get("text") or "").strip()

    def _transcribe_whisper(self, wave: np.ndarray) -> str:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import]
        except ImportError:
            print("[Luna] faster-whisper not installed, falling back to Vosk.", file=sys.stderr, flush=True)
            self._transcribe_backend = "vosk"
            return self._transcribe_vosk(wave)
        if self._whisper_model is None:
            model_size = str(getattr(config, "LUNA_WHISPER_MODEL", "tiny.en"))
            print(f"[Luna] Loading Whisper model {model_size!r}…", flush=True)
            self._whisper_model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=self._whisper_cpu_threads,
            )
        audio = np.asarray(wave, dtype=np.float32).reshape(-1)
        segments, _ = self._whisper_model.transcribe(audio, language="en", beam_size=1)
        return " ".join(s.text.strip() for s in segments).strip()

    def _preload_whisper_if_enabled(self) -> None:
        if self._transcribe_backend != "whisper":
            return
        if not bool(getattr(config, "LUNA_WHISPER_PRELOAD", True)):
            return
        if self._whisper_model is not None:
            return

        def _load() -> None:
            try:
                from faster_whisper import WhisperModel  # type: ignore[import]
            except ImportError:
                return
            try:
                model_size = str(getattr(config, "LUNA_WHISPER_MODEL", "tiny.en"))
                print(f"[Luna] Preloading Whisper model {model_size!r}…", flush=True)
                self._whisper_model = WhisperModel(
                    model_size,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=self._whisper_cpu_threads,
                )
            except Exception:
                # Best-effort preload; transcription will retry on first use.
                return

        threading.Thread(target=_load, daemon=True).start()

    def _transcribe(self, wave: np.ndarray) -> str:
        if self._transcribe_backend == "whisper":
            return self._transcribe_whisper(wave)
        return self._transcribe_vosk(wave)

    def _trim_history(self) -> None:
        max_turns = max(1, int(config.LUNA_CHAT_MAX_HISTORY_TURNS))
        if len(self._history) >= max_turns:
            self._history = self._history[-max_turns:]

    def _build_prompt(
        self,
        user_text: str,
        *,
        search_context: str | None = None,
        verbosity: str = "brief",
    ) -> str:
        footer = (
            "Reply as Luna in clear spoken English (no markdown). "
            f"Verbosity: {verbosity.upper()}. "
            "Use QUICK vs DEEP length per your system instructions."
        )
        lines: list[str] = []
        if search_context:
            lines.append(f"[Web search result: {search_context}]")
        if self._history:
            for u, a in self._history:
                lines.append(f"User said: {u}")
                lines.append(f"You (Luna) replied: {a}")
            lines.append(f"User now says: {user_text}")
        else:
            lines.append(user_text)
        lines.append(footer)
        return "\n".join(lines)

    def _speak(self, text: str, *, speed: float = 1.0) -> None:
        speak_weather(
            text,
            tts_backend=self._tts_backend,
            output_volume=self._vol,
            spotify_duck_volume=None,
            spotify_duck_halve=self._spotify_duck_halve,
            tts_quiet=True,
            # Barge-in kills afplay; do not read the same line with macOS ``say``.
            say_fallback=False,
            speed=speed,
            # Use sounddevice with a live-gain callback so the UI volume slider
            # takes effect mid-utterance instead of only on the next reply.
            live_volume=True,
        )

    @staticmethod
    def _tts_speed_for(text: str) -> float:
        """Pick a Kokoro speed based on config and reply length."""
        cfg = str(getattr(config, "LUNA_TTS_SPEED", "auto")).strip().lower()
        if cfg != "auto":
            try:
                return max(0.5, min(2.0, float(cfg)))
            except ValueError:
                pass
        words = len(text.split())
        if words <= 25:
            return 1.1
        if words >= 80:
            return 0.95
        return 1.0

    def _chat_system(self, *, verbosity: str = "brief") -> str:
        base = config.LUNA_CHAT_SYSTEM.strip()
        if verbosity == "brief":
            base = f"{base}\n\nYou are in BRIEF mode. Keep answers short unless user asks for more detail."
        elif verbosity == "detailed":
            base = f"{base}\n\nYou are in DETAILED mode. Give the complete thorough answer."
        if self._completed_turns >= int(config.LUNA_CHAT_OFFER_END_AFTER_TURNS):
            return f"{base}\n\n{config.LUNA_CHAT_SYSTEM_LONG_DIALOGUE.strip()}"
        return base

    def _count_paragraphs(self, text: str) -> int:
        paras = [p.strip() for p in re.split(r"\n\s*\n+", (text or "").strip())]
        return sum(1 for p in paras if p)

    def _play_thinking_chime(self) -> None:
        p = config.resolve_luna_thinking_wav_path()
        if p is None:
            print(
                "[Luna] Thinking chime: no thinking.wav found (add jarvis/audio_assets/thinking/thinking.wav "
                "or project audio_assets/).",
                flush=True,
            )
            return
        mac_audio.play_vocals_wav(p)

    def _stream_and_speak(
        self,
        prompt: str,
        system: str,
        my_gen: int,
        *,
        max_sentences: int | None = None,
    ) -> str:
        """
        Stream tokens from Ollama into a background thread, collecting the full reply.
        Once all text is received, speak it in a SINGLE Kokoro call — one WAV, one afplay,
        no inter-sentence gaps regardless of reply length.

        The "still thinking" chime fires at the halfway timeout if nothing has arrived yet.
        """
        sentence_q: _queue.Queue[str | None] = _queue.Queue()
        errors: list[Exception] = []
        stop_event = threading.Event()

        def _producer() -> None:
            try:
                for sentence in ollama_generate_stream(
                    host=self._ollama_host,
                    model=self._ollama_model,
                    system=system,
                    prompt=prompt,
                    temperature=float(config.LUNA_CHAT_TEMPERATURE),
                    timeout_s=float(config.LUNA_CHAT_TIMEOUT_S),
                    max_sentences=max_sentences,
                ):
                    if stop_event.is_set():
                        break
                    sentence_q.put(sentence)
            except Exception as e:
                errors.append(e)
            finally:
                sentence_q.put(None)

        producer = threading.Thread(target=_producer, daemon=True)
        producer.start()

        parts: list[str] = []
        got_first = False
        halfway_s = float(config.LUNA_CHAT_TIMEOUT_S) / 2
        t0 = time.perf_counter()
        t_first: float | None = None

        while True:
            wait = halfway_s if not got_first else None
            try:
                sentence = sentence_q.get(timeout=wait)
            except _queue.Empty:
                if my_gen == self._session_generation:
                    self._speak("Still thinking, one moment.")
                sentence = sentence_q.get()

            if sentence is None:
                break
            if my_gen != self._session_generation:
                stop_event.set()
                producer.join(timeout=2.0)
                return ""
            if not got_first:
                self._set_ui_state("speaking")
            got_first = True
            if t_first is None:
                t_first = time.perf_counter()
            parts.append(sentence)
            if self._ui_token_callback and my_gen == self._session_generation:
                try:
                    self._ui_token_callback(sentence)
                except Exception:
                    pass

        producer.join(timeout=5.0)
        if errors:
            raise errors[0]

        reply = " ".join(parts)
        if my_gen == self._session_generation:
            dt_first = None if t_first is None else max(0.0, t_first - t0)
            dt_total = max(0.0, time.perf_counter() - t0)
            print(
                f"[Luna] Ollama: sentences={len(parts)} first_s={dt_first!s} total_s={dt_total:.2f} "
                f"chars={len(reply)}",
                flush=True,
            )
        if reply and my_gen == self._session_generation:
            speed = self._tts_speed_for(reply)
            self._speak(reply, speed=speed)
        return reply

    def _pipeline(self, wave: np.ndarray, *, pre_transcribed: str | None = None) -> None:
        my_gen = self._session_generation
        try:
            if pre_transcribed is not None:
                user_text = pre_transcribed
                print(f"[Luna] You (text): {user_text}", flush=True)
            else:
                user_text = self._transcribe(wave)
                if my_gen != self._session_generation:
                    return
                raw_stt = user_text
                user_text = normalize_command_transcript(
                    user_text,
                    enabled=bool(getattr(config, "LUNA_STT_FUZZY_NORMALIZE", True)),
                )
                print(f"[Luna] You (transcribed): {raw_stt}", flush=True)
                if user_text != raw_stt:
                    print(f"[Luna] Normalized (commands): {user_text}", flush=True)
                if user_text and self._ui_text_callback:
                    try:
                        self._ui_text_callback(user_text, "")
                    except Exception:
                        pass
            if not user_text:
                if pre_transcribed is None:
                    print("[Luna] (didn't catch that — try again)", flush=True)
                    self._begin_followup_listening()
                return

            if _is_abort_listening_command(user_text):
                print("[Luna] Cancelled.", flush=True)
                with self._lock:
                    self._history.clear()
                    self._completed_turns = 0
                    self._pending_calendar_add = None
                    self._pending_plan_overwrite = None
                    self._phase = "idle"
                self._set_ui_state("idle")
                self._invoke_idle_reset()
                return

            if _should_end_session(user_text):
                print("[Luna] Goodbye.", flush=True)
                self._speak("Alright. Talk soon.")
                with self._lock:
                    history_snapshot = list(self._history)
                    self._history.clear()
                    self._completed_turns = 0
                    self._pending_calendar_add = None
                    self._pending_plan_overwrite = None
                    self._phase = "idle"
                self._set_ui_state("idle")
                if history_snapshot:
                    self._save_long_term_memory(history_snapshot)
                self._invoke_idle_reset()
                return

            # Progressive disclosure: user asked for more detail on the last question.
            if self._maybe_handle_elaboration(user_text, my_gen):
                return

            # Plan overwrite confirmation: user said yes/overwrite or new version.
            if self._pending_plan_overwrite is not None:
                topic = self._pending_plan_overwrite
                self._pending_plan_overwrite = None
                normalized = _normalize_utterance(user_text)
                words = set(normalized.split())
                overwrite_words = {"yes", "yeah", "yep", "sure", "ok", "okay", "overwrite", "replace", "update"}
                version_words = {"new", "version", "another"}
                if words & overwrite_words:
                    reply = self._generate_and_save_plan(topic, my_gen, overwrite=True)
                elif (words & version_words) or "new version" in normalized:
                    reply = self._generate_and_save_plan(topic, my_gen, version=True)
                else:
                    reply = "Okay, I'll leave the existing plan as is."
                    self._speak(reply)
                    self._begin_followup_listening()
                    return
                if my_gen != self._session_generation:
                    return
                if reply:
                    with self._lock:
                        if my_gen == self._session_generation:
                            self._history.append((user_text, reply))
                            self._trim_history()
                            self._completed_turns += 1
                    if self._ui_text_callback:
                        try:
                            self._ui_text_callback(user_text, reply)
                        except Exception:
                            pass
                self._begin_followup_listening()
                return

            # Slot-filling for calendar adds: accept missing fields over multiple turns.
            if self._pending_calendar_add is not None:
                p_title, p_date, p_start, p_end = self._pending_calendar_add

                # Try to fill date
                if p_date is None:
                    d0 = calendar_events.parse_date_token(user_text)
                    if d0 is not None:
                        p_date = d0.isoformat()

                # Try to fill title
                if p_title is None:
                    # Accept "work" or "the name is work" style.
                    m = re.search(r"\b(?:called|named|titled|name is|call it)\s+(.+)$", user_text, re.IGNORECASE)
                    if m:
                        p_title = (m.group(1) or "").strip(' "\'.,')
                    else:
                        # If it's short, treat whole utterance as the title.
                        t = (user_text or "").strip(' "\'.,')
                        if 1 <= len(t.split()) <= 5:
                            p_title = t

                # Try to fill times (rare followup)
                if p_start is None:
                    st = _extract_time_token(user_text)
                    if st:
                        p_start = st

                if p_end is None:
                    # Only attempt to set an end time if the user actually said two times
                    # (e.g. "5 to 6", "5pm-6pm"). Otherwise keep duration-based default.
                    raw = (user_text or "").strip().lower()
                    if raw:
                        found: list[str] = []
                        for m in re.finditer(
                            r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm)\b|\b\d{1,2}:\d{2}\b",
                            raw,
                        ):
                            tok = m.group(0).replace(".", "").strip()
                            if calendar_events.parse_time_token(tok) is not None:
                                found.append(tok)
                        if len(found) >= 2:
                            p_end = found[1]

                # If we have enough, create the event now.
                if p_title and p_date and p_start:
                    self._pending_calendar_add = None
                    d = calendar_events.parse_date_token(p_date)
                    st = calendar_events.parse_time_token(p_start)
                    et = calendar_events.parse_time_token(p_end) if p_end else None
                    if d is None or st is None:
                        print("[Luna] Command handled: Couldn't parse date/time.", flush=True)
                        self._speak("I couldn't parse the date or time.")
                        self._begin_followup_listening()
                        return
                    start_dt = datetime.combine(d, st)
                    if et is not None:
                        end_dt = datetime.combine(d, et)
                        if end_dt <= start_dt:
                            end_dt = start_dt + timedelta(minutes=60)
                    else:
                        mins = self._decide_event_duration_minutes(p_title)
                        end_dt = start_dt + timedelta(minutes=mins)

                    ok, err = calendar_events.create_event(title=p_title, start=start_dt, end=end_dt)
                    if ok:
                        print("[Luna] Command handled: Event added.", flush=True)
                        self._speak("Event added.")
                    else:
                        print("[Luna] Command handled: Calendar add failed.", flush=True)
                        if err and ("not authorized" in err.lower() or "not authorised" in err.lower()):
                            self._speak("I need Calendar permission for Terminal or Cursor.")
                        else:
                            self._speak("I couldn't add that event.")
                    self._begin_followup_listening()
                    return

                # Still missing something: ask the next most important slot.
                self._pending_calendar_add = (p_title, p_date, p_start, p_end)
                if p_start is None:
                    self._speak("What time should it start?")
                elif p_date is None:
                    self._speak("What day should I schedule it? Say today, tomorrow, or a date like 2026-04-17.")
                elif p_title is None:
                    self._speak("What should I call the event?")
                self._begin_followup_listening()
                return

            cmd_ok, cmd_spoken, release_mic = try_voice_command(
                user_text,
                notion_app=self._notion_app,
                notion_fullscreen=self._notion_fullscreen,
                duration_decider=self._decide_event_duration_minutes,
                speak_fn=self._speak,
            )
            if cmd_ok:
                print(f"[Luna] Command handled: {cmd_spoken}", flush=True)
                if self._ui_text_callback and cmd_spoken:
                    try:
                        self._ui_text_callback(user_text, cmd_spoken)
                    except Exception:
                        pass
                if cmd_spoken:
                    self._speak(cmd_spoken)
                if release_mic:
                    self._release_mic_for_external_dictation()
                    threading.Thread(
                        target=cursor_mode.fire_macos_dictation_handoff,
                        kwargs={"settle_s": 0.35},
                        daemon=True,
                    ).start()
                    return
                self._begin_followup_listening()
                return

            partial = try_parse_add_event_missing_date(user_text)
            if partial is not None:
                title, start_time_token, end_time_token = partial
                # Store and ask for the missing slot locally (no LLM).
                self._pending_calendar_add = (title, None, start_time_token, end_time_token)
                print("[Luna] Command handled: Missing event day.", flush=True)
                self._speak("What day should I schedule it? Say today, tomorrow, or a date like 2026-04-17.")
                self._begin_followup_listening()
                return

            partial2 = try_parse_add_event_partial(user_text)
            if partial2 is not None:
                title, date_token, start_time_token, end_time_token = partial2
                # Start slot-filling locally for missing title/date/time.
                self._pending_calendar_add = (title, date_token, start_time_token, end_time_token)
                print("[Luna] Command handled: Calendar add needs more info.", flush=True)
                if start_time_token is None:
                    self._speak("What time should it start?")
                elif date_token is None:
                    self._speak("What day should I schedule it? Say today, tomorrow, or a date like 2026-04-17.")
                elif title is None:
                    self._speak("What should I call the event?")
                self._begin_followup_listening()
                return

            # Check for plan generation intent before general LLM call.
            plan_match = _PLAN_RE.search(user_text)
            if plan_match:
                topic = plan_match.group(1).strip().rstrip(".")
                from jarvis.services.plans import plan_path as _plan_path
                existing = _plan_path(topic)
                if existing.exists():
                    # Ask user: overwrite or create a new version?
                    self._pending_plan_overwrite = topic
                    fname = existing.name
                    self._speak(
                        f"A file called {fname} already exists. "
                        f"Should I overwrite it or create a new version?"
                    )
                    self._begin_followup_listening()
                    return
                reply = self._generate_and_save_plan(topic, my_gen)
                if my_gen != self._session_generation:
                    return
                if reply:
                    with self._lock:
                        if my_gen == self._session_generation:
                            self._history.append((user_text, reply))
                            self._trim_history()
                            self._completed_turns += 1
                    if self._ui_text_callback:
                        try:
                            self._ui_text_callback(user_text, reply)
                        except Exception:
                            pass
                if my_gen != self._session_generation:
                    return
                self._begin_followup_listening()
                return

            q = self._explicit_web_search_query(user_text)
            search_ctx = self._search_ddg(q) if q else None
            if search_ctx:
                print(f"[Luna] Web search result injected: {search_ctx[:80]}…", flush=True)

            if self._is_ambiguous_user_text(user_text):
                self._speak("I didn’t catch that clearly. Could you say it again, or add one detail?")
                self._begin_followup_listening()
                return

            # Default: brief mode (cap to ~2 sentences). Detailed only when asked.
            self._last_user_question = user_text
            self._last_search_ctx = search_ctx
            prompt = self._build_prompt(user_text, search_context=search_ctx, verbosity="brief")
            system = self._chat_system(verbosity="brief")
            reply = self._stream_and_speak(prompt, system, my_gen, max_sentences=2)
            if my_gen != self._session_generation:
                return
            if reply:
                print(f"[Luna] Reply: {reply}", flush=True)
                with self._lock:
                    if my_gen == self._session_generation:
                        self._history.append((user_text, reply))
                        self._trim_history()
                        self._completed_turns += 1
                if self._ui_text_callback:
                    try:
                        self._ui_text_callback(user_text, reply)
                    except Exception:
                        pass
            if my_gen != self._session_generation:
                return
            self._begin_followup_listening()
        except (URLError, ConnectionRefusedError, OSError) as ex:
            is_conn = isinstance(ex, (URLError, ConnectionRefusedError)) or (
                "connection refused" in str(ex).lower()
            )
            print(f"[Luna] chat error: {ex}", file=sys.stderr, flush=True)
            try:
                if my_gen == self._session_generation:
                    msg = "My AI backend appears to be offline." if is_conn else "Sorry, I could not answer that."
                    self._speak(msg)
            except Exception:
                pass
            with self._lock:
                self._history.clear()
                self._completed_turns = 0
                self._phase = "idle"
            self._set_ui_state("idle")
            self._invoke_idle_reset()
        except Exception as ex:  # noqa: BLE001
            print(f"[Luna] chat error: {ex}", file=sys.stderr, flush=True)
            try:
                if my_gen == self._session_generation:
                    self._speak("Sorry, I could not answer that.")
            except Exception:
                pass
            with self._lock:
                self._history.clear()
                self._completed_turns = 0
                self._phase = "idle"
            self._set_ui_state("idle")
            self._invoke_idle_reset()

