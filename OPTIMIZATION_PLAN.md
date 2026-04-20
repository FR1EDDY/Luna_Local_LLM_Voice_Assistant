# Optimization Plan (Jarvis / Luna)

> Goal: make the assistant feel reliable, fast after wake, and low-overhead while always-on.

---

## Success criteria

- [ ] Double-clap can be triggered repeatedly in a single app run (no “one-shot” behavior).
- [ ] Wake → “Listening…” feels immediate and consistent.
- [ ] Wake → first spoken response is noticeably faster (especially on first use after launch).
- [ ] Always-on CPU stays low and stable (no periodic spikes).
- [ ] No “surprise” background network calls unless explicitly requested.

---

## Phase 0 — Baseline + measurement (do before heavy refactors)

- [ ] Add a simple timing log around key stages:
  - [ ] app start → UI shown
  - [ ] wake detected → mic collecting starts
  - [ ] end-of-speech detected → transcription finished
  - [ ] transcription finished → command handled OR Ollama request started
  - [ ] Ollama request started → first token OR full reply ready
  - [ ] reply ready → audio playback starts
  - [ ] playback starts → playback ends
- [ ] Record “typical” numbers for:
  - [ ] first wake after launch
  - [ ] subsequent wakes
  - [ ] command-only interaction (Spotify/volume)
  - [ ] LLM answer interaction

---

## Phase 1 — Reliability / UX “must fix” (highest ROI)

### 1) Double-clap re-trigger bug

- [ ] Fix the state reset so double-clap works repeatedly.
- [ ] Confirm it still respects cooldown and doesn’t spam-trigger.

### 2) Reduce accidental session terminations

- [ ] Review silence thresholds (`LUNA_CMD_END_SILENCE_S`, pre-speech timeout, max duration).
- [ ] Ensure “wake greeting → user speaking” isn’t being cut off by post-wake gap timing.

---

## Phase 2 — Always-on CPU and callback hygiene

### 1) Keep the audio callback lightweight

- [ ] Ensure the sounddevice callback path avoids:
  - [ ] large Python allocations per 20ms block
  - [ ] repeated list appends of large arrays when not needed
  - [ ] heavy string/regex operations per block
- [ ] If any expensive work happens in callback:
  - [ ] move it to a queue + worker thread

### 2) Throttle UI mic-level updates

- [ ] Update UI level at a fixed rate (e.g. 20–30 Hz), not on every audio block.
- [ ] Confirm the visualizer still feels smooth.

---

## Phase 3 — Wake word path optimization (Vosk)

- [ ] Feed wake-word detector with fewer Python calls (batch blocks where possible).
- [ ] Avoid “match work” on every partial transcript:
  - [ ] only check on final segments or when confidence is meaningful
- [ ] Reduce false positives by tightening the alias/matching rules instead of increasing compute.

---

## Phase 4 — Wake → first response latency

### 1) Whisper (command transcription) strategy

- [ ] Option A: Default to `tiny.en` for speed; fall back to `base.en` if transcript looks weak.
- [ ] Option B: Keep `base.en`, but preload Whisper at startup (like Kokoro prewarm).
- [ ] Confirm memory usage is acceptable.

### 2) Ollama latency strategy

- [ ] **Progressive response policy (reduce perceived latency + reduce “LLM rambling”)**
  - [ ] Default to **short answers** (1–2 sentences) unless the user explicitly asks for detail.
  - [ ] If confidence is low / transcript is ambiguous: ask a **clarifying question** instead of apologizing or hallucinating.
    - [ ] Example: “Did you mean X or Y?” / “Do you want help with A or B?”
  - [ ] If the user’s question would trigger a long explanation:
    - [ ] Give a **brief summary first** (high-level idea + next step)
    - [ ] End with: “Want the detailed version?” / “Should I elaborate?”
  - [ ] Implementation notes:
    - [ ] Add a “verbosity budget” to the Ollama prompt (e.g. `brief|normal|detailed`) and start with `brief`.
    - [ ] Add a simple **uncertainty/ambiguity heuristic** (short transcript, low STT confidence, many unknown words) → force “clarify” mode.
    - [ ] Keep a follow-up mechanism: if user says “yes / elaborate / more detail”, re-ask the LLM in `detailed` mode using the same context.
  - [ ] Measurement target:
    - [ ] For most interactions, **time-to-first-audio** should improve because the model generates fewer tokens.
    - [ ] Track average reply length + latency vs. user follow-up rate (“elaborate”).

---

## Phase 5 — Web search (performance + privacy)

Current issue: search triggers on weak cues (“today”, “what is”, etc.) and can do multiple network calls.

- [ ] Make web search opt-in:
  - [ ] only run when user explicitly says “search for …”
  - [ ] or add a CLI/env toggle to disable it by default
- [ ] Add caching per session:
  - [ ] same query shouldn’t re-fetch
- [ ] Cap injected context length so it doesn’t drown small local models.

---

## Phase 6 — Cleanup / reduce “appendix” complexity

- [ ] Remove or fully wire “thinking chime” + “long reply cue” (currently dead/broken).
- [ ] Consider deleting low-value commands/integrations:
  - [ ] `open youtube` feature (if not used)
- [ ] Remove “open Calendar/Weather apps after double-clap” (keep spoken briefing; avoid focus stealing).
- [ ] Remove stale docs/config references after changes.

---

## Implementation order (recommended)

1. Double-clap reset fix
2. Disable/opt-in web search + add caching
3. UI mic-level throttling
4. Whisper strategy (tiny default or preload)
5. Ollama model/timeout tuning
6. Cleanup dead features and unused integrations

---

## Test plan (manual)

- [ ] Start app, trigger double clap 5 times over 2 minutes → welcome should run each time (with cooldown).
- [ ] Say “Luna” → speak a command (“volume 50”) → response should be near-instant.
- [ ] Ask a non-command question → confirm Ollama path works and audio plays.
- [ ] Confirm no markdown files auto-open (no RStudio popups).
- [ ] Confirm CPU stays reasonable while idle (no periodic spikes).

