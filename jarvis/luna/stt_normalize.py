"""Cheap transcript cleanup for command recognition (no LLM).

Uses exact aliases plus Levenshtein distance against a small command vocabulary.
Designed for ~10–30 word utterances; cost is negligible vs Vosk/Ollama.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Whole-phrase fixes first (Vosk often splits / mis-hears multi-word product names).
_PHRASE_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bopen\s+you\s+true\b", re.I), "open youtube"),
    (re.compile(r"\bopen\s+you\s+tune\b", re.I), "open youtube"),
    (re.compile(r"\bopen\s+your\s+tube\b", re.I), "open youtube"),
    (re.compile(r"\bhoping\s+you\s+tube\b", re.I), "open youtube"),
    (re.compile(r"\bhoping\s+youtube\b", re.I), "open youtube"),
    (re.compile(r"\bactivate\s+them\s+old\s+cursor\b", re.I), "activate cursor mode"),
)

# Single-token fixes that ASR often gets wrong (exact match, lowercased).
_EXACT_TOKEN_FIX: dict[str, str] = {
    "curse": "cursor",
    "curser": "cursor",
    "cursour": "cursor",
    "cursur": "cursor",
    "wether": "weather",
    "spotafy": "spotify",
    "spotify's": "spotify",
    "you-tube": "youtube",
    "utube": "youtube",
    "youtu": "youtube",
    "calender": "calendar",
    "miting": "meeting",
    "meting": "meeting",
}

# (canonical_word, max_edit_distance). Ties (same best distance) → no change.
# Keep max distance tight for short words to avoid mangling normal English.
_FUZZY_TARGETS: tuple[tuple[str, int], ...] = (
    ("cursor", 2),
    ("weather", 1),
    ("spotify", 2),
    ("notion", 2),
    ("youtube", 2),
    ("calendar", 2),
    ("schedule", 2),
    ("volume", 2),
    ("activate", 2),
    ("tomorrow", 2),
    ("today", 1),
    ("location", 2),
)


@lru_cache(maxsize=256)
def _levenshtein_cached(a: str, b: str) -> int:
    return _levenshtein_impl(a, b)


def _levenshtein_impl(s: str, t: str) -> int:
    if s == t:
        return 0
    if not s:
        return len(t)
    if not t:
        return len(s)
    # One row DP (shorter string outer for smaller memory)
    if len(s) > len(t):
        s, t = t, s
    m, n = len(s), len(t)
    prev = list(range(n + 1))
    for i, cs in enumerate(s, 1):
        cur = [i]
        for j, ct in enumerate(t, 1):
            cur.append(
                min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (0 if cs == ct else 1),
                )
            )
        prev = cur
    return prev[n]


def _snap_word(word_lower: str) -> str | None:
    """If word is within edit distance of a single best target, return canonical; else None."""
    if len(word_lower) < 3:
        return None

    max_for: dict[str, int] = {}
    for canon, max_d in _FUZZY_TARGETS:
        # Tightest bound wins if a canon appears twice.
        max_for[canon] = min(max_d, max_for.get(canon, 99))

    best_d = 99
    best_canon: str | None = None
    tie = False

    for canon, max_d in max_for.items():
        d = _levenshtein_cached(word_lower, canon)
        if d > max_d:
            continue
        if d < best_d:
            best_d = d
            best_canon = canon
            tie = False
        elif d == best_d and canon != best_canon:
            tie = True

    if tie or best_canon is None or best_d >= 99 or best_d == 0:
        return None
    return best_canon


# Words + non-words so we preserve spacing and punctuation.
_TOKEN_RE = re.compile(r"[A-Za-z]+|[^A-Za-z\s]+|\s+")


def normalize_command_transcript(text: str, *, enabled: bool = True) -> str:
    """
    Normalize STT text so ``try_voice_command`` regexes match more often.

    ``enabled`` defaults True; pass ``False`` or set ``JARVIS_LUNA_STT_FUZZY=0`` via config at call site.
    """
    if not enabled or not (text or "").strip():
        return text

    s = text
    for pat, repl in _PHRASE_FIXES:
        s = pat.sub(repl, s)

    out: list[str] = []
    for m in _TOKEN_RE.finditer(s):
        chunk = m.group(0)
        if not chunk.isalpha():
            out.append(chunk)
            continue
        lw = chunk.lower()
        if lw in _EXACT_TOKEN_FIX:
            fixed = _EXACT_TOKEN_FIX[lw]
            out.append(_swap_case_like(chunk, fixed))
            continue
        snapped = _snap_word(lw)
        if snapped:
            out.append(_swap_case_like(chunk, snapped))
        else:
            out.append(chunk)
    return "".join(out)


def _swap_case_like(template: str, replacement_lower: str) -> str:
    """Lowercase replacement; if template was ALLCAPS or Title, mimic lightly."""
    if template.isupper() and template.isalpha():
        return replacement_lower.upper()
    if len(template) > 0 and template[0].isupper():
        return replacement_lower.capitalize()
    return replacement_lower
