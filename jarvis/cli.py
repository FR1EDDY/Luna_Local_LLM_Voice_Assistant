"""
CLI: double clap → Spotify + optional vocals WAV + IP weather (Open-Meteo) + TTS.

Wake word LUNA (Vosk) is **on by default** on the same mic stream; replies are random ``.wav`` files
from ``audio assets`` (see ``--luna-audio-dir`` / ``--no-voice-assistant``).

macOS + Spotify desktop app. Set default URIs in ``jarvis.config``. Default TTS is local Kokoro
(Python 3.12); optional OpenAI speech via ``--tts-backend openai``.
"""

from __future__ import annotations

import argparse
import functools
import threading
import os
import sys
from pathlib import Path

try:
    import sounddevice as sd
except ImportError:
    exe = sys.executable
    print(
        "Missing sounddevice for the Python you used to start Jarvis.\n"
        f"  interpreter: {exe}\n"
        f"  {exe} -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise

from jarvis import config
from jarvis.integrations import mac_audio, spotify as spotify_integration
from jarvis.listener import run_double_clap_listener
from jarvis.services import weather
from jarvis.services.tts import log_tts_startup_summary
from jarvis.welcome import run_welcome_sequence


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Double clap → Spotify (macOS).")
    p.add_argument(
        "--track",
        default=config.DEFAULT_TRACK_URI,
        help="Track URI/URL to play (default: a track). Ignored if --playlist is set.",
    )
    p.add_argument(
        "--playlist",
        default=config.DEFAULT_PLAYLIST_URI,
        help="Optional playlist URI/URL to play instead of --track.",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=config.THRESHOLD_RMS,
        help=f"RMS clap threshold (default {config.THRESHOLD_RMS}).",
    )
    p.add_argument("--list-devices", action="store_true", help="List mic devices and exit.")
    p.add_argument("--device", type=int, default=None, help="Input device index.")
    p.add_argument("-v", "--verbose", action="store_true", help="Print RMS when loud.")
    p.add_argument(
        "--play-vocals",
        action="store_true",
        help=(
            "Play the local vocals WAV after the printed briefing and before weather TTS "
            "(default off; see jarvis.config.DEFAULT_PLAY_VOCALS_WAV and JARVIS_PLAY_VOCALS)."
        ),
    )
    p.add_argument(
        "--no-vocals",
        action="store_true",
        help="Do not play the local vocals WAV (default; overrides --play-vocals).",
    )
    p.add_argument(
        "--vocals",
        type=Path,
        default=config.DEFAULT_VOCALS_WAV,
        help="WAV to play with Spotify.",
    )
    p.add_argument("--no-weather", action="store_true", help="Skip weather + speech.")
    p.add_argument(
        "--spotify-startup-volume",
        type=int,
        default=None,
        metavar="0-100",
        help="Optional: set Spotify in-app volume when the listener starts (default: do not change).",
    )
    p.add_argument(
        "--skip-spotify-startup-volume",
        action="store_true",
        help="Do not change Spotify volume when the listener starts.",
    )
    p.add_argument(
        "--spotify-tts-duck-volume",
        type=int,
        default=None,
        metavar="0-100",
        help=(
            "Optional: absolute Spotify volume while the voice plays (0–100), restored afterward. "
            "Default: duck to half of current volume."
        ),
    )
    p.add_argument(
        "--weather-announcement-volume",
        type=int,
        default=config.DEFAULT_WEATHER_ANNOUNCEMENT_VOLUME,
        metavar="0-300",
        help=(
            "Announcement loudness: 100 = baseline, higher = louder "
            "(>100 uses ffmpeg if installed). Default "
            f"{config.DEFAULT_WEATHER_ANNOUNCEMENT_VOLUME}."
        ),
    )
    p.add_argument(
        "--spotify-activate",
        action="store_true",
        help="Bring Spotify to the front when starting a track (default: background).",
    )
    p.add_argument(
        "--no-notion",
        action="store_true",
        help="Do not open Notion after Spotify starts.",
    )
    p.add_argument(
        "--notion-app",
        default="Notion",
        help="Application name for open -a (default: Notion).",
    )
    p.add_argument(
        "--notion-fullscreen",
        action="store_true",
        help="After Notion opens, send Ctrl+Cmd+F (Accessibility required for System Events).",
    )
    p.add_argument(
        "--pre-announcement-delay",
        type=float,
        default=config.PRE_ANNOUNCEMENT_DELAY_S,
        metavar="SECONDS",
        help=(
            f"Wait this many seconds after Spotify/Notion/vocals before TTS "
            f"(default {config.PRE_ANNOUNCEMENT_DELAY_S}; see PRE_ANNOUNCEMENT_DELAY_S in jarvis.config)."
        ),
    )
    p.add_argument(
        "--tts-backend",
        choices=("openai", "kokoro"),
        default=(os.environ.get("JARVIS_TTS_BACKEND") or config.DEFAULT_TTS_BACKEND).strip().lower(),
        help=(
            "Weather voice: kokoro (local hexgrad/Kokoro-82M, default) or openai (cloud). "
            "Default from JARVIS_TTS_BACKEND or jarvis.config.DEFAULT_TTS_BACKEND."
        ),
    )
    p.add_argument(
        "--llm-backend",
        choices=("none", "ollama"),
        default=(os.environ.get("JARVIS_LLM_BACKEND") or config.DEFAULT_LLM_BACKEND).strip().lower(),
        help=(
            "Briefing rewrite: none (raw composed text) or ollama (local). "
            "Default from JARVIS_LLM_BACKEND or jarvis.config.DEFAULT_LLM_BACKEND."
        ),
    )
    p.add_argument(
        "--ollama-host",
        default=(os.environ.get("JARVIS_OLLAMA_HOST") or config.DEFAULT_OLLAMA_HOST).strip(),
        help="Ollama host URL (default from JARVIS_OLLAMA_HOST or jarvis.config.DEFAULT_OLLAMA_HOST).",
    )
    p.add_argument(
        "--ollama-model",
        default=(os.environ.get("JARVIS_OLLAMA_MODEL") or config.DEFAULT_OLLAMA_MODEL).strip(),
        help="Ollama model name (default from JARVIS_OLLAMA_MODEL or jarvis.config.DEFAULT_OLLAMA_MODEL).",
    )
    p.add_argument(
        "--ollama-test",
        action="store_true",
        help="Ping Ollama with a tiny prompt and print the result, then exit.",
    )
    p.add_argument(
        "--weather-text-once",
        action="store_true",
        help="Print the (optionally LLM-rewritten) weather briefing once, then exit.",
    )
    p.add_argument(
        "--voice-assistant",
        action=argparse.BooleanOptionalAction,
        default=config.DEFAULT_VOICE_ASSISTANT,
        help=(
            "Wake word LUNA (offline Vosk) + random reply WAV from --luna-audio-dir "
            "(default: on; set jarvis.config.DEFAULT_VOICE_ASSISTANT or use --no-voice-assistant). "
            "``--luna-response-wav`` still forces a single file when set."
        ),
    )
    p.add_argument(
        "--luna-audio-dir",
        type=Path,
        default=config.LUNA_RESPONSE_AUDIO_DIR,
        help="Folder of WAV replies; one file is chosen at random per wake (default: jarvis/audio_assets).",
    )
    p.add_argument(
        "--luna-response-wav",
        type=Path,
        default=None,
        help="If set, always play this WAV instead of random choice from --luna-audio-dir.",
    )
    p.add_argument(
        "--luna-chat",
        action=argparse.BooleanOptionalAction,
        default=config.DEFAULT_LUNA_CHAT,
        help=(
            "After LUNA: listen, transcribe (Vosk), answer via Ollama (fast model), speak + print reply "
            "(default: on with voice-assistant; use --no-luna-chat for wake chime only). "
            "Uses the same Ollama model as --ollama-model."
        ),
    )
    p.add_argument(
        "--luna-voice-rms",
        type=float,
        default=None,
        metavar="RMS",
        help=(
            "Luna mic sensitivity: RMS threshold to treat audio as speech (lower = more sensitive to quiet input). "
            "Default: JARVIS_LUNA_CMD_VOICE_RMS if set, else jarvis.config (see LUNA_CMD_VOICE_RMS)."
        ),
    )
    p.add_argument(
        "--luna-mic-gain",
        type=float,
        default=None,
        metavar="MULT",
        help=(
            "Luna digital mic boost (linear, applied before speech detection + Vosk). "
            "1.0 = off; ~2–3 helps quiet voices. Default: JARVIS_LUNA_MIC_GAIN or jarvis.config.LUNA_MIC_GAIN."
        ),
    )
    p.add_argument(
        "--luna-post-tts-gap",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            f"Silence after Luna speaks before mic reopens (default {config.LUNA_POST_TTS_GAP_S}s). "
            "Lower values feel snappier; headsets can go as low as 0.3s."
        ),
    )
    p.add_argument(
        "--luna-post-wake-gap",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            f"Silence after wake chime before mic opens (default {config.LUNA_POST_WAKE_GAP_S}s)."
        ),
    )
    p.add_argument(
        "--luna-transcribe-backend",
        choices=("vosk", "whisper"),
        default="whisper",
        help=(
            "Transcription engine for Luna commands: whisper (default, better accuracy; requires: pip install faster-whisper) or "
            "vosk (always-on offline fallback, no extra deps)."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run the full pipeline but skip TTS and Spotify. "
            "Prints what would be spoken instead. Useful for prompt/command iteration."
        ),
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity (default: INFO). DEBUG prints detailed pipeline traces.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    from jarvis.logger import setup_logging
    setup_logging(args.log_level)

    config.validate_config()

    if args.dry_run:
        from jarvis.services.tts import set_dry_run
        set_dry_run(True)
        print("[DRY-RUN] TTS disabled — will print spoken text instead.", flush=True)

    from jarvis.services.env_watcher import start_env_watcher
    start_env_watcher()

    if args.list_devices:
        print(sd.query_devices())
        return

    if args.ollama_test:
        from jarvis.services.llm_ollama import ollama_generate

        out = ollama_generate(
            host=args.ollama_host,
            model=args.ollama_model,
            system="You are a helpful assistant. Reply in one short sentence.",
            prompt="Say hello and confirm you are reachable via Ollama.",
            temperature=0.0,
            timeout_s=20.0,
        )
        print(out, flush=True)
        return

    if args.weather_text_once:
        from jarvis.services.llm import rewrite_weather_if_enabled

        wb = args.llm_backend.strip().lower()
        if wb == "ollama":
            print(
                f"[LLM] rewrite backend=ollama host={args.ollama_host} model={args.ollama_model}",
                flush=True,
            )
        text = weather.compose_weather_speech_or_error()
        text = rewrite_weather_if_enabled(
            text,
            llm_backend=args.llm_backend,
            ollama_host=args.ollama_host,
            ollama_model=args.ollama_model,
        )
        print(text, flush=True)
        return

    if not args.no_weather:
        print(weather.OPEN_METEO_ATTRIBUTION, flush=True)

    tts_backend = args.tts_backend.strip().lower()
    llm_backend = args.llm_backend.strip().lower()
    log_tts_startup_summary(tts_backend)
    if llm_backend == "ollama":
        print(
            f"[LLM] rewrite backend=ollama host={args.ollama_host} model={args.ollama_model} "
            "(env: JARVIS_LLM_BACKEND, JARVIS_OLLAMA_HOST, JARVIS_OLLAMA_MODEL; disable with --llm-backend none)",
            flush=True,
        )
    else:
        print("[LLM] rewrite disabled (llm-backend=none).", flush=True)
    if tts_backend == "kokoro" and (
        not args.no_weather or (args.voice_assistant and args.luna_chat)
    ):
        from jarvis.services.tts_kokoro import prewarm_kokoro_at_startup

        prewarm_kokoro_at_startup()

    if (
        sys.platform == "darwin"
        and args.spotify_startup_volume is not None
        and not args.skip_spotify_startup_volume
    ):
        startup_vol = max(0, min(100, int(args.spotify_startup_volume)))
        spotify_integration.set_sound_volume(startup_vol)

    if args.luna_post_tts_gap is not None:
        config.LUNA_POST_TTS_GAP_S = max(0.1, float(args.luna_post_tts_gap))
    if args.luna_post_wake_gap is not None:
        config.LUNA_POST_WAKE_GAP_S = max(0.1, float(args.luna_post_wake_gap))

    duck = None if args.spotify_tts_duck_volume is None else max(0, min(100, int(args.spotify_tts_duck_volume)))
    pre_ann = float(args.pre_announcement_delay)
    weather_vol = max(0, min(300, int(args.weather_announcement_volume))) / 100.0
    vocals_path = args.vocals.expanduser().resolve()
    spotify_target = args.playlist or args.track

    play_vocals = bool(config.DEFAULT_PLAY_VOCALS_WAV)
    ev = (os.environ.get("JARVIS_PLAY_VOCALS") or "").strip().lower()
    if ev in ("1", "true", "yes", "on"):
        play_vocals = True
    elif ev in ("0", "false", "no", "off"):
        play_vocals = False
    if args.play_vocals:
        play_vocals = True
    if args.no_vocals:
        play_vocals = False
    no_vocals = not play_vocals

    _welcome_kwargs = dict(
        spotify_uri=spotify_target,
        vocals_path=vocals_path,
        no_vocals=no_vocals,
        no_weather=args.no_weather,
        tts_backend=tts_backend,
        llm_backend=llm_backend,
        ollama_host=args.ollama_host,
        ollama_model=args.ollama_model,
        tts_duck_volume=duck,
        spotify_activate=args.spotify_activate,
        weather_output_volume=weather_vol,
        open_notion_after_spotify=not args.no_notion,
        notion_app=args.notion_app,
        notion_fullscreen=args.notion_fullscreen,
        pre_announcement_delay_s=pre_ann,
    )

    def on_double():
        headlines = run_welcome_sequence(**_welcome_kwargs)
        if luna_chat and headlines:
            luna_chat.set_news_context(headlines)

    wake_detector = None
    on_wake_word = None
    luna_chat = None
    if args.voice_assistant:
        from jarvis.wake_word import WakeWordDetector

        luna_dir = args.luna_audio_dir.expanduser().resolve()
        wake_detector = WakeWordDetector(
            model_dir=config.VOSK_MODEL_DIR,
            sample_rate=config.SAMPLE_RATE,
            phrase=config.WAKE_WORD_PHRASE,
            aliases=config.WAKE_WORD_ALIASES,
        )
        _wake_tokens = sorted(
            {config.WAKE_WORD_PHRASE.strip().lower(), *[x.strip().lower() for x in config.WAKE_WORD_ALIASES if x.strip()]}
        )
        print(f"[Voice] Wake tokens (Vosk): {', '.join(_wake_tokens)}", flush=True)
        greeting_fn: functools.partial | None = None
        fixed = args.luna_response_wav
        if fixed is not None:
            fixed_path = fixed.expanduser().resolve()
            greeting_fn = functools.partial(mac_audio.play_vocals_wav, fixed_path)
            on_wake_word = greeting_fn
            print(
                f"[Voice] Wake word {config.WAKE_WORD_PHRASE.upper()} → {fixed_path}",
                flush=True,
            )
        else:
            if luna_dir.resolve() == config.LUNA_RESPONSE_AUDIO_DIR.resolve():
                # Prefer the classic SA phrases for the wake greeting, but keep the parent dir
                # and legacy project folders as additional sources.
                scan_dirs = (
                    luna_dir / getattr(config, "LUNA_WAKE_AUDIO_SUBDIR", "sa"),
                    luna_dir,
                    *config.LUNA_RESPONSE_AUDIO_DIR_EXTRAS,
                )
            else:
                scan_dirs = (luna_dir,)
            wavs = mac_audio.list_wavs_in_dirs(scan_dirs)
            greeting_fn = functools.partial(
                mac_audio.play_random_wav_from_dir,
                scan_dirs,
                fallback=config.DEFAULT_LUNA_RESPONSE_WAV,
                extra_fallbacks=config.LUNA_RESPONSE_EXTRA_FALLBACK_WAVS,
                exclude_names=config.LUNA_WAKE_RANDOM_EXCLUDE_WAVS,
            )
            on_wake_word = greeting_fn
            fb = config.DEFAULT_LUNA_RESPONSE_WAV
            extra = ""
            if not wavs:
                extra = f" (no wav in scanned folders — will try fallbacks e.g. {fb.name})"
            print(
                f"[Voice] Wake word {config.WAKE_WORD_PHRASE.upper()} → random from {scan_dirs} "
                f"({len(wavs)} wav(s)){extra}",
                flush=True,
            )

        _ui_level_cb = None
        if args.luna_chat and greeting_fn is not None:
            from jarvis.luna.chat import LunaVoiceFollowup
            from jarvis.gui import push_ui_state, push_ui_text, push_ui_token, push_ui_level, push_ui_plan
            _ui_level_cb = push_ui_level

            def _luna_idle_reset_wake() -> None:
                wake_detector.reset_after_wake()

            luna_thr = (
                float(args.luna_voice_rms)
                if args.luna_voice_rms is not None
                else float(config.LUNA_CMD_VOICE_RMS)
            )
            luna_gain_show = config.clamp_luna_mic_gain(
                float(args.luna_mic_gain) if args.luna_mic_gain is not None else float(config.LUNA_MIC_GAIN)
            )
            luna_chat = LunaVoiceFollowup(
                greeting_fn=greeting_fn,
                sample_rate=config.SAMPLE_RATE,
                vosk_model_dir=config.VOSK_MODEL_DIR,
                ollama_host=args.ollama_host,
                ollama_model=args.ollama_model,
                tts_backend=tts_backend,
                output_volume=weather_vol,
                spotify_duck_halve=True,
                idle_reset_fn=_luna_idle_reset_wake,
                notion_app=args.notion_app,
                notion_fullscreen=args.notion_fullscreen,
                ui_state_callback=push_ui_state,
                ui_text_callback=push_ui_text,
                ui_token_callback=push_ui_token,
                ui_level_callback=push_ui_level,
                ui_plan_callback=push_ui_plan,
                voice_rms_threshold=args.luna_voice_rms,
                mic_gain=args.luna_mic_gain,
                transcribe_backend=args.luna_transcribe_backend,
            )
            on_wake_word = None
            print(
                f"[Luna] follow-up chat: Vosk dir={config.VOSK_MODEL_DIR}, "
                f"voice_rms={luna_thr} (lower = more sensitive), mic_gain×{luna_gain_show}, "
                f"Ollama model={args.ollama_model!r} (same as --ollama-model) host={args.ollama_host!s}",
                flush=True,
            )

    # Push the listener to a background thread:
    def _listener_loop():
        run_double_clap_listener(
            threshold=args.threshold,
            device=args.device,
            verbose=args.verbose,
            on_double_clap=on_double,
            wake_detector=wake_detector,
            on_wake_word=on_wake_word,
            luna_chat=luna_chat,
            ui_level_callback=_ui_level_cb,
        )
    threading.Thread(target=_listener_loop, daemon=True).start()

    # Occupy the main thread with the OS-native UI window
    from jarvis.gui import start_luna_ui
    start_luna_ui(luna_chat)
