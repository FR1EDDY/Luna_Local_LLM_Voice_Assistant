# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
python3 main.py           # Standard entry point
python3 -m jarvis         # Alternative (same result)
```

Both converge on `jarvis.cli.main()`, which parses args, starts a background listener thread, and blocks on the PyWebView GUI window.

### Useful CLI flags for development

```bash
python3 main.py --list-devices              # List available mic devices
python3 main.py --device <int>              # Select input device
python3 main.py --no-voice-assistant        # Disable wake word detection
python3 main.py --no-weather                # Skip weather briefing
python3 main.py --weather-text-once         # Print weather then exit (fast test)
python3 main.py --ollama-test               # Test Ollama connectivity then exit
python3 main.py --tts-backend openai        # Use OpenAI TTS instead of local Kokoro
python3 main.py --llm-backend none          # Disable LLM weather rewrite
python3 main.py --threshold 0.15            # Adjust clap detection sensitivity
python3 main.py --luna-mic-gain 3.0         # Boost mic input for quiet environments
```

## Architecture Overview

### Request Flow (Double-Clap → Weather → Luna Chat)

1. **`listener.py`** — captures 16 kHz mono audio in 20 ms blocks, computes RMS energy, detects two loud peaks within 60–450 ms (double-clap), enforces 2.5 s cooldown
2. **`wake_word.py`** — runs Vosk offline speech recognition in parallel; listens for "LUNA" wake phrase with whole-word matching
3. **`welcome.py`** — orchestrates the post-trigger sequence: Spotify playback (AppleScript), Notion launch, vocals WAV, weather briefing via Open-Meteo + Ollama rewrite, TTS speech
4. **`luna/chat.py`** — enters multi-turn dialogue: Vosk transcription → command parsing (`luna/commands.py`) → Ollama fallback → TTS reply; loops until silence or goodbye

### Key Modules

- **`jarvis/config.py`** — all defaults (sample rate, thresholds, model paths, URIs). Override with `JARVIS_*` env vars; CLI args take highest priority.
- **`jarvis/paths.py`** — filesystem anchors (project root, models dir, audio assets)
- **`jarvis/services/`** — weather, TTS, calendar, LLM backends (each independently replaceable)
- **`jarvis/integrations/`** — macOS-specific: AppleScript Spotify control, EventKit calendar, PyWebView GUI, Quartz window management
- **`jarvis/luna/`** — voice assistant subsystem: STT normalization, command dispatch, Ollama dialogue
- **`ui/`** — PyWebView frontend (HTML/CSS/JS, glassmorphism visualizer, frameless window)

### Configuration Hierarchy

`jarvis/config.py` defaults → `JARVIS_*` environment variables → CLI arguments

### Audio Pipeline

- All audio: 16 kHz, mono, float32 in [-1, 1]
- Clap detection uses RMS energy thresholding
- Digital gain (`--luna-mic-gain`) applied only to Luna's speech path
- Vosk model lives at `models/vosk-model-small-en-us-0.15/`

### macOS Integration Approach

- Spotify: controlled entirely via `osascript` AppleScript (no Spotify SDK)
- Calendar: PyObjC `EventKit` framework
- Window management: PyObjC `Quartz` framework
- HTTP calls use stdlib `urllib` only (no third-party HTTP library)

### TTS Backends

- **Kokoro** (default): local 82M-param model, requires Python < 3.13
- **OpenAI**: cloud TTS, requires `OPENAI_API_KEY` in `.env`
- **System `say`**: fallback, no dependencies

### LLM Backends

- **Ollama** (default): local model server at `http://localhost:11434`, model `gemma4:e4b`
- **None**: disables LLM weather rewrite

## Dependencies

Requires Python 3.12 (Kokoro is skipped on 3.13+). Install with:

```bash
pip install -r requirements.txt
```

Key packages: `sounddevice`, `vosk`, `kokoro`, `pywebview`, `pyobjc-framework-EventKit`, `pyobjc-framework-Quartz`, `openai`, `numpy`, `soundfile`

## Environment Variables

Create a `.env` file in the project root:

```
OPENAI_API_KEY=...       # For OpenAI TTS or LLM
ELEVENLABS_API_KEY=...   # If using ElevenLabs TTS
```

`JARVIS_*`-prefixed env vars can override any `config.py` default without code changes.
