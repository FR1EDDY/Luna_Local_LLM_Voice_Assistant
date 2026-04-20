"""Defaults for the double-clap welcome flow (edit here or override via CLI)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from jarvis.paths import PACKAGE_DIR, PROJECT_ROOT


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


# Audio detection
SAMPLE_RATE = 16_000
BLOCK_DURATION_S = 0.02
THRESHOLD_RMS = 0.12
MIN_GAP_MS = 60
MAX_GAP_MS = 450
COOLDOWN_S = 2.5

# Pause after Spotify / Notion / optional vocals WAV, before the weather TTS starts.
PRE_ANNOUNCEMENT_DELAY_S = 1.0

# 100 = nominal TTS level; >100 needs ffmpeg for boost (see services.tts).
DEFAULT_WEATHER_ANNOUNCEMENT_VOLUME = 135

DEFAULT_VOCALS_WAV = PROJECT_ROOT / "Vocals Audio.wav"

# Voice assistant: Vosk model (see https://alphacephei.com/vosk/models) and WAV(s) on wake word.
# ``small`` is fast but error-prone; download ``vosk-model-en-us-0.22`` (or larger) and point VOSK_MODEL_DIR for fewer mis-hears.
VOSK_MODEL_DIR = PROJECT_ROOT / "models" / "vosk-model-small-en-us-0.15"
# Default: ``jarvis/audio_assets/`` (next to this package). Renamed / distinct ``.wav`` names work fine.
LUNA_RESPONSE_AUDIO_DIR = PACKAGE_DIR / "audio_assets"
# Default per-wake greeting lives under the "sa" subfolder (classic Jarvis-style acknowledgements).
LUNA_WAKE_AUDIO_SUBDIR = "sa"
# Older layouts: also scanned when the default dir is used (see ``cli``).
LUNA_RESPONSE_AUDIO_DIR_EXTRAS = (
    PROJECT_ROOT / "audio assets",
    PROJECT_ROOT / "audio_assets",
)
# Last-resort file if every scanned folder is empty (pick any real filename you keep there).
DEFAULT_LUNA_RESPONSE_WAV = LUNA_RESPONSE_AUDIO_DIR / LUNA_WAKE_AUDIO_SUBDIR / "Yes_what_can_I_do.wav"
# Never use these for the random per-wake chime (``thinking.wav`` is for heavy-reply feedback only).
LUNA_WAKE_RANDOM_EXCLUDE_WAVS: tuple[str, ...] = ("thinking.wav",)
LUNA_RESPONSE_EXTRA_FALLBACK_WAVS: tuple[Path, ...] = (
    PROJECT_ROOT / "Kokoro TTS Audio.wav",
    LUNA_RESPONSE_AUDIO_DIR / "Kokoro TTS Audio.wav",
    # Common layout: SA phrases live in a subfolder.
    LUNA_RESPONSE_AUDIO_DIR / LUNA_WAKE_AUDIO_SUBDIR / "Yes_what_can_I_do.wav",
)
WAKE_WORD_PHRASE = "luna"
# Acoustic aliases matched with the same whole-word pattern as WAKE_WORD_PHRASE.
# Each fires the wake trigger identically to the primary phrase.
#   "loona" — Vosk frequently collapses the /ju/ vowel in "luna" → /uː/, transcribing "loona".
#   "lune"  — Vosk drops the final /ə/ when speech is fast or mic gain is low.
#   "lunar" — Vosk appends /r/ (rhotic intrusion); the original pattern blocks it, but it's
#              a valid mishear worth accepting. NOTE: will also fire on the word "lunar" alone
#              in any context, which is an intentional tradeoff.
# Override at runtime: JARVIS_WAKE_WORD_ALIASES=luna,loona,lune,lunar (comma-separated).
# "luna" is always matched regardless; listing it here has no effect (dedup is automatic).
WAKE_WORD_ALIASES: list[str] = list(
    {s.strip().lower() for s in
     (os.environ.get("JARVIS_WAKE_WORD_ALIASES") or "luna,loona,lune,lunar").split(",")
     if s.strip()}
)
# How long after a successful wake trigger before another can fire (prevents double-firing
# from a single utterance echoing through Vosk's partial → final sequence).
# Already wired in listener.py; listed here so it follows the same pattern as COOLDOWN_S.
WAKE_WORD_COOLDOWN_S = max(0.5, _env_float("JARVIS_WAKE_WORD_COOLDOWN_S", 2.0))
# LUNA / Vosk wake listener when running ``python3 main.py`` (CLI: ``--no-voice-assistant`` to disable).
DEFAULT_VOICE_ASSISTANT = True

# After LUNA: multi-turn chat → Vosk transcribe → Ollama (``--ollama-model``) → TTS.
# Say the wake word again while Luna is active to stop playback and restart (barge-in).
DEFAULT_LUNA_CHAT = True
# Shorter gaps feel snappier; raise if you get echo picking up Luna’s own voice.
LUNA_POST_WAKE_GAP_S = max(0.1, _env_float("JARVIS_LUNA_POST_WAKE_GAP_S", 0.75))
# Pause after Luna speaks (Kokoro) before opening the mic again (reduces echo).
LUNA_POST_TTS_GAP_S = max(0.05, _env_float("JARVIS_LUNA_POST_TTS_GAP_S", 0.35))
LUNA_CMD_MAX_DURATION_S = 30.0
LUNA_CMD_END_SILENCE_S = max(0.4, _env_float("JARVIS_LUNA_CMD_END_SILENCE_S", 1.0))
# No speech yet, right after the wake chime.
LUNA_CMD_PRE_SPEECH_TIMEOUT_S = 6.0
# After at least one exchange: silence this long → session ends; wake word “Luna” works again.
LUNA_CHAT_FOLLOWUP_PRE_SPEECH_TIMEOUT_S = 10.0


def _env_int_bounded(name: str, default: int, lo: int, hi: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        v = default
    else:
        try:
            v = int(raw, 10)
        except ValueError:
            v = default
    return max(lo, min(hi, v))


# RSS headlines in the welcome briefing (Markdown + spoken). Override: JARVIS_BRIEFING_HEADLINE_COUNT.
BRIEFING_HEADLINE_COUNT = _env_int_bounded("JARVIS_BRIEFING_HEADLINE_COUNT", 8, 1, 25)


def clamp_luna_mic_gain(raw: float) -> float:
    """Keep Luna digital gain in a sane range (over-amplification adds hiss / clipping)."""
    return max(0.25, min(8.0, float(raw)))


# Linear gain applied only on the Luna capture path (speech gate + Vosk). 1.0 = unchanged.
# >1.0 boosts quiet microphones so you do not have to speak as loudly (may pick up more room noise).
LUNA_MIC_GAIN = clamp_luna_mic_gain(_env_float("JARVIS_LUNA_MIC_GAIN", 2.5))

# Lower = more sensitive (detects quieter speech but can trigger on noise / echo).
# Works together with LUNA_MIC_GAIN: softer default + gain for normal conversation volume.
# Override: JARVIS_LUNA_CMD_VOICE_RMS=0.003 or python3 main.py --luna-voice-rms 0.003
LUNA_CMD_VOICE_RMS = _env_float("JARVIS_LUNA_CMD_VOICE_RMS", 0.010)
# Fast STT cleanup (exact map + Levenshtein on a small word list) before command regex — no LLM.
LUNA_STT_FUZZY_NORMALIZE = _env_bool("JARVIS_LUNA_STT_FUZZY", True)
LUNA_CHAT_MAX_HISTORY_TURNS = 10
# After this many completed back-and-forths, the model may optionally invite wrapping up (not every reply).
LUNA_CHAT_OFFER_END_AFTER_TURNS = 6
# Tiered depth: simple practical questions stay short and snappy; technical / precision topics get longer prose.
LUNA_CHAT_SYSTEM = (
    "You are Luna, a voice assistant. Output is read aloud—no markdown, no bullet lists, no emojis.\n"
    "You understand natural speech, including long questions, partial sentences, and informal phrasing. "
    "Always try to infer intent even if phrasing is imperfect; never say you don't understand something "
    "that has an obvious meaning.\n"
    "If the user's request is unclear or ambiguous, ask ONE short clarifying question instead of guessing.\n"
    "Judge each question and choose answer depth:\n"
    "• QUICK (default for everyday, practical, or simple factual questions—e.g. hydration, how much of "
    "something, quick tips, short definitions, name/time-style facts): give a **brisk** answer—about "
    "**1–2 tight sentences**. Lead with the direct answer, then add **one** useful line of context or nuance. "
    "No long setup, no exhaustive edge cases, no mini-essay.\n"
    "• DEEP (technical, financial/legal precision, multi-step how-it-works, proofs, detailed comparisons, "
    "storytelling, or when the user clearly wants a thorough explanation or asks you to elaborate): give a "
    "**fuller** spoken explanation—several paragraphs of connected prose if needed, step-by-step when useful. "
    "Do not cut the answer short; finish the thought completely.\n"
    "When unsure, prefer **QUICK** unless the user obviously needs depth.\n"
    "For questions that could take a long explanation, give a QUICK summary first and end with a short offer like "
    "'Want the detailed version?' only when it would genuinely help.\n"
    "Use prior conversation context to avoid asking for information already given. "
    "Do not ask after every answer if they need anything else; just answer. Long-dialogue wrap-up rules "
    "apply only when noted below."
)
LUNA_CHAT_SYSTEM_LONG_DIALOGUE = (
    "This dialogue already has many exchanges. Keep answering substantively. You may add at most one short "
    "optional sentence inviting them to keep going or say they're done—do not repeat that every turn; if they "
    "just asked a normal follow-up, only answer it."
)
LUNA_CHAT_TEMPERATURE = 0.3
LUNA_CHAT_TIMEOUT_S = 30.0
# Spoken goodbyes / “done” (word-boundary regex). User can also say only: no, nope, nah, no thanks.
LUNA_CHAT_END_SESSION_PATTERN = (
    r"\b(that'?s all|that is all|nothing else|no more|we'?re done|we are done|all done|"
    r"i'?m done|good enough|goodbye|bye luna|bye for now|stop listening|stop there|"
    r"thank you good\s*bye|thanks good\s*bye|that will be all|we can stop)\b"
)

# ``thinking.wav`` in ``jarvis/audio_assets/``: instant feedback before a heavy Ollama reply.
LUNA_THINKING_CHIME_ENABLED = True
LUNA_THINKING_CHIME_WAV = LUNA_RESPONSE_AUDIO_DIR / "thinking.wav"
# Play ``thinking.wav`` as soon as Ollama starts when any trigger matches (see below).
LUNA_THINKING_CHIME_IMMEDIATE = True
# Fire if the composed prompt is at least this long (0 = skip this trigger).
LUNA_THINKING_CHIME_MIN_PROMPT_CHARS = 320
# Fire if the transcribed user line is at least this long (0 = skip; catches long questions).
LUNA_THINKING_CHIME_MIN_USER_CHARS = 64
# Fire if there are already this many prior Q&A pairs in memory (0 = skip this trigger).
LUNA_THINKING_CHIME_MIN_HISTORY_TURNS = 0
# If the user says any of these (substring, lowercased), fire the chime (long-answer cues).
LUNA_THINKING_CHIME_USER_CUES: tuple[str, ...] = (
    "explain",
    "explanation",
    "describe",
    "detailed",
    "in detail",
    "elaborate",
    "how does",
    "how do",
    "why does",
    "what is a ",
    "what are ",
    "tell me about",
    "walk me through",
    "comparison",
    "compare",
    "versus",
    "difference between",
)
# If > 0, play ``thinking.wav`` again after this many seconds if Ollama is still running.
LUNA_THINKING_CHIME_AFTER_S = 0.0

# If enabled: after the model reply is generated (but before TTS), play an SA phrase when the reply
# contains multiple paragraphs (blank-line separated). This catches cases the pre-prompt heuristics miss.
LUNA_LONG_REPLY_CUE_ENABLED = True
LUNA_LONG_REPLY_CUE_MIN_PARAGRAPHS = 2


def resolve_luna_thinking_wav_path() -> Path | None:
    """First existing ``thinking.wav`` under the package assets dir or legacy project folders."""
    dirs = (LUNA_RESPONSE_AUDIO_DIR, *LUNA_RESPONSE_AUDIO_DIR_EXTRAS)
    candidates: list[Path] = [LUNA_THINKING_CHIME_WAV]
    candidates.extend(d / "thinking.wav" for d in dirs)
    # Support the common layout: jarvis/audio_assets/thinking/thinking.wav
    candidates.extend(d / "thinking" / "thinking.wav" for d in dirs)
    seen: set[Path] = set()
    for raw in candidates:
        p = raw.expanduser().resolve()
        if p in seen:
            continue
        seen.add(p)
        if p.is_file():
            return p
    return None


# If True, play DEFAULT_VOCALS_WAV after the briefing text is printed and before weather TTS.
# Override with --play-vocals / --no-vocals or JARVIS_PLAY_VOCALS=1|0.
DEFAULT_PLAY_VOCALS_WAV = False

DEFAULT_TRACK_URI = "spotify:track:39shmbIHICJ2Wxnk1fPSdz"
#DEFAULT_PLAYLIST_URI = (
#    "spotify:playlist:5KxlF2sD0XzE4racT442qI?si=4b45296bbcfb4771"
#)
DEFAULT_PLAYLIST_URI = (
    'spotify:playlist:3jzszJ1ix9swNf9KmDgZfI?si=d28ca6ac39174eba' # for jarvis playlist
)
# Text-to-speech: Kokoro only (local). OpenAI TTS removed.
DEFAULT_TTS_BACKEND = "kokoro"

# Optional LLM rewrite for the spoken briefing. Override with --llm-backend or JARVIS_LLM_BACKEND.
# "ollama" = local rewrite via http://localhost:11434; "none" = use the raw composed text.
DEFAULT_LLM_BACKEND = "ollama"

# Ollama defaults (override via env or CLI).
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma4:e4b"

# Supported Python for full installs including Kokoro (see repo root ``.python-version``).
RECOMMENDED_PYTHON = (3, 12)

# Kokoro (https://github.com/hexgrad/kokoro): defaults match the upstream README examples.
KOKORO_REPO_ID = "hexgrad/Kokoro-82M"
KOKORO_LANG_CODE = "a"  # American English G2P; see Kokoro docs for other codes.
KOKORO_VOICE = "af_heart"# could also use af_jessica
#KOKORO_VOICE = "bm_lewis" #for Alfred Voice use bm_lewis 


# Kokoro startup prewarm (see ``tts_kokoro.prewarm_kokoro_at_startup``):
#   JARVIS_KOKORO_NO_PREWARM=1 — skip loading weights until first double-clap (faster "Listening.").
#   JARVIS_KOKORO_DEEP_PREWARM=1 — also run one tiny synthesis at startup (slowest start, fastest first line).

# Cursor IDE — Luna voice command ``activate mode cursor`` (open app, focus AI chat, dictation best-effort).
# JARVIS_CURSOR_APP — app name for ``open -a`` (default ``Cursor``).
# JARVIS_CURSOR_CHAT_KEY — ``cmd+l`` (AI Chat), ``cmd+i`` (Composer), or ``cmd+shift+l``.
# JARVIS_DICTATION_KEYCODE / JARVIS_DICTATION_TAPS — optional; if macOS dictation uses the mic key only,
#   set shortcut to "press Fn key twice" in System Settings, or set KEYCODE (e.g. from Key Codes.app) and TAPS (1 or 2).
CURSOR_APP_NAME = (os.environ.get("JARVIS_CURSOR_APP") or "Cursor").strip()
CURSOR_CHAT_KEYSTROKE = (os.environ.get("JARVIS_CURSOR_CHAT_KEY") or "cmd+l").strip().lower()


def _env_positive_int(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        n = int(raw, 10)
    except ValueError:
        return None
    return n if n > 0 else None


CURSOR_DICTATION_KEYCODE = _env_positive_int("JARVIS_DICTATION_KEYCODE")
CURSOR_DICTATION_TAPS = _env_positive_int("JARVIS_DICTATION_TAPS")

# Whisper transcription backend (optional, requires: pip install faster-whisper).
# Default ``tiny.en`` prioritizes latency; use ``base.en`` for harder transcripts.
LUNA_WHISPER_MODEL = (os.environ.get("JARVIS_LUNA_WHISPER_MODEL") or "tiny.en").strip()

# If true, load the Whisper model early (in the background) so the first Luna command
# doesn’t pay the full model initialization cost.
LUNA_WHISPER_PRELOAD = _env_bool("JARVIS_LUNA_WHISPER_PRELOAD", True)

# Kokoro TTS playback speed for Luna dialogue.
# 1.0 = normal; 1.1 = slightly snappier for short replies; 0.95 = slower for long DEEP answers.
# "auto" detects based on reply word count.
LUNA_TTS_SPEED = (os.environ.get("JARVIS_LUNA_TTS_SPEED") or "auto").strip().lower()


# Directory where Luna writes generated markdown files (plans, notes).
# Override: JARVIS_MARKDOWN_OUTPUT_DIR=~/Documents/notes
MARKDOWN_OUTPUT_DIR: Path = Path(
    os.environ.get("JARVIS_MARKDOWN_OUTPUT_DIR") or (Path.home() / "Luna" / "notes")
).expanduser()


def validate_config() -> None:
    """Sanity-check config values at startup; prints warnings instead of crashing."""
    issues: list[str] = []
    if MIN_GAP_MS >= MAX_GAP_MS:
        issues.append(f"MIN_GAP_MS ({MIN_GAP_MS}) >= MAX_GAP_MS ({MAX_GAP_MS})")
    if LUNA_CMD_END_SILENCE_S >= LUNA_CMD_MAX_DURATION_S:
        issues.append(
            f"LUNA_CMD_END_SILENCE_S ({LUNA_CMD_END_SILENCE_S}) >= "
            f"LUNA_CMD_MAX_DURATION_S ({LUNA_CMD_MAX_DURATION_S})"
        )
    if not VOSK_MODEL_DIR.exists():
        issues.append(f"VOSK_MODEL_DIR not found: {VOSK_MODEL_DIR}")
    if not (0.25 <= LUNA_MIC_GAIN <= 8.0):
        issues.append(f"LUNA_MIC_GAIN ({LUNA_MIC_GAIN}) outside safe range [0.25, 8.0]")
    for msg in issues:
        print(f"[Config] WARNING: {msg}", file=sys.stderr, flush=True)
