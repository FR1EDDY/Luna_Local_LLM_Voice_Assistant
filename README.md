# Luna — Local LLM Voice Assistant

macOS voice assistant: double-clap wake, Spotify + briefing flow, **LUNA** wake word (Vosk), and multi-turn chat with **Ollama** + local or cloud TTS. See [`CLAUDE.md`](CLAUDE.md) for architecture and CLI flags.

## Requirements

- **Python 3.12** (Kokoro TTS is skipped on 3.13+)
- **macOS** (Spotify/Notion/EventKit integrations use Apple APIs)
- **[Ollama](https://ollama.com/)** running locally for chat (default: model from `jarvis/config.py`, overridable with `JARVIS_OLLAMA_MODEL` / CLI)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Vosk model (wake word STT)

Download [Vosk small English](https://alphacephei.com/vosk/models) (or a larger model for accuracy) and extract it so this path exists:

`models/vosk-model-small-en-us-0.15/`

Use that folder name or set **`VOSK_MODEL_DIR`** in [`jarvis/config.py`](jarvis/config.py) to match your unpack path.

### Environment

Copy env vars as needed (optional cloud TTS/LLM):

```bash
# OPENAI_API_KEY=...
# ELEVENLABS_API_KEY=...
```

See **`JARVIS_*`** overrides in `jarvis/config.py`.

### Optional root WAV

If you use the default welcome vocals path, place your own file at **`Vocals Audio.wav`** in the project root (ignored by Git) or pass **`--vocals`** / env to point elsewhere.

## Run

```bash
python3 main.py
```

Useful flags: `--list-devices`, `--device <n>`, `--no-voice-assistant`, `--ollama-test`, `--tts-backend openai`. Full list in `jarvis/cli.py` / `CLAUDE.md`.

## License

Add a `LICENSE` file when you choose one (e.g. MIT).
