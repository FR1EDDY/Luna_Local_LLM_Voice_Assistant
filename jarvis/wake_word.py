"""Vosk-based wake word detection (offline, local)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

try:
    from vosk import KaldiRecognizer, Model
except ImportError:
    Model = None  # type: ignore[misc, assignment]
    KaldiRecognizer = None  # type: ignore[misc, assignment]


def wake_word_pattern(phrase: str) -> re.Pattern[str]:
    """Whole-word match, case-insensitive (avoids false positives like *lunar*)."""
    esc = re.escape(phrase.strip().lower())
    return re.compile(rf"\b{esc}\b", re.IGNORECASE)


def wake_word_pattern_union(phrases: list[str]) -> re.Pattern[str]:
    """
    Match any of several wake tokens as whole words (longer tokens first in the alternation).
    Used so acoustic aliases like "loona" / "lune" actually trigger the same as "luna".
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for p in phrases:
        s = (p or "").strip().lower()
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)
    if not cleaned:
        cleaned = ["luna"]
    cleaned.sort(key=len, reverse=True)
    inner = "|".join(re.escape(p) for p in cleaned)
    return re.compile(rf"\b(?:{inner})\b", re.IGNORECASE)


def _barge_in_stop_pattern(phrases: list[str]) -> re.Pattern[str]:
    """Stop/restart phrases while a session is active — supports every wake alias."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for p in phrases:
        s = (p or "").strip().lower()
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)
    if not cleaned:
        cleaned = ["luna"]
    cleaned.sort(key=len, reverse=True)
    inner = "|".join(re.escape(p) for p in cleaned)
    return re.compile(
        rf"\b(?:{inner})\s+(?:stop|restart|cancel|quit|enough)|stop\s+(?:{inner})|cancel\s+(?:{inner})\b",
        re.IGNORECASE,
    )


class WakeWordDetector:
    """Feeds 16 kHz mono float32 [-1, 1] blocks into Vosk; reports when the wake phrase is spoken."""

    def __init__(
        self,
        *,
        model_dir: Path,
        sample_rate: int,
        phrase: str = "luna",
        aliases: list[str] | None = None,
    ) -> None:
        if Model is None or KaldiRecognizer is None:
            print(
                "Wake word needs the `vosk` package.\n"
                f"  {sys.executable} -m pip install vosk",
                file=sys.stderr,
            )
            raise SystemExit(2)
        path = model_dir.expanduser().resolve()
        if not path.is_dir():
            print(f"Vosk model directory not found: {path}", file=sys.stderr)
            raise SystemExit(2)
        tokens: list[str] = [phrase.strip().lower()]
        if aliases:
            for a in aliases:
                s = (a or "").strip().lower()
                if s and s not in tokens:
                    tokens.append(s)
        self._wake_tokens = tokens
        self._pat = wake_word_pattern_union(tokens)
        self._stop_pat = _barge_in_stop_pattern(tokens)
        self._rec = KaldiRecognizer(Model(str(path)), sample_rate)
        # Vosk partial hypotheses repeat the same word for many consecutive blocks; only treat
        # a new match as a trigger when the phrase *appears* (rising edge), not every frame.
        self._had_phrase_match: bool = False
        self._had_stop_match: bool = False

    def _text_from_json(self, s: str) -> str:
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return ""
        if not isinstance(obj, dict):
            return ""
        return (obj.get("text") or obj.get("partial") or "").strip()

    def process_block(self, mono_float32: np.ndarray) -> tuple[bool, bool]:
        """
        Push one block of mono float32 samples.
        Returns (wake_triggered, stop_triggered):
          wake_triggered — bare wake phrase first appeared (use to start a session from idle)
          stop_triggered — explicit stop/restart phrase detected (use to barge-in during a session)
        Both are rising-edge: True only on the frame they first appear.
        """
        if mono_float32.size == 0:
            return False, False
        pcm = np.clip(mono_float32.astype(np.float64) * 32767.0, -32768, 32767).astype(np.int16).tobytes()
        if self._rec.AcceptWaveform(pcm):
            text = self._text_from_json(self._rec.Result())
        else:
            text = self._text_from_json(self._rec.PartialResult())
        matches_wake = bool(text) and bool(self._pat.search(text))
        matches_stop = bool(text) and bool(self._stop_pat.search(text))
        wake_rising = matches_wake and not self._had_phrase_match
        stop_rising = matches_stop and not self._had_stop_match
        self._had_phrase_match = matches_wake
        self._had_stop_match = matches_stop
        return wake_rising, stop_rising

    def reset_after_wake(self) -> None:
        """Clear decoder state so playback / room noise does not keep stale *luna* partials."""
        self._had_phrase_match = False
        self._had_stop_match = False
        self._rec.Reset()
