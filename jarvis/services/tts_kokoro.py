"""Local Kokoro TTS (hexgrad/Kokoro-82M) for weather announcements — default voice; no cloud TTS API."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import numpy as np

from jarvis import config
from jarvis.services.tts import _load_dotenv, _play_media_with_volume, _say, _spotify_duck

_SAMPLE_RATE_HZ = 24_000
# PyPI kokoro>=0.9.x: Requires-Python >=3.10,<3.13
_KOKORO_UNSUPPORTED_PY = (3, 13)

_pipeline_lock = threading.Lock()
_pipeline_cache: dict[tuple[str, str], Any] = {}
_logged_pipeline_key: tuple[str, str] | None = None


def _kokoro_env() -> tuple[str, str, str]:
    """lang_code, voice id, Hugging Face repo_id."""
    lang = (os.environ.get("JARVIS_KOKORO_LANG") or config.KOKORO_LANG_CODE).strip().lower()
    voice = (os.environ.get("JARVIS_KOKORO_VOICE") or config.KOKORO_VOICE).strip()
    repo = (os.environ.get("JARVIS_KOKORO_REPO") or config.KOKORO_REPO_ID).strip()
    return lang, voice, repo


def _weights_filename(repo_id: str) -> str:
    try:
        from kokoro.model import KModel

        return KModel.MODEL_NAMES.get(repo_id, "(see repo config.json)")
    except Exception:
        return "(unknown)"


def _get_pipeline(lang: str, repo_id: str):
    global _logged_pipeline_key
    key = (lang, repo_id)
    with _pipeline_lock:
        if key in _pipeline_cache:
            return _pipeline_cache[key]
        from kokoro import KPipeline

        # Explicit repo_id avoids Kokoro's defaulting warning on stderr.
        pipeline = KPipeline(lang_code=lang, repo_id=repo_id)
        _pipeline_cache[key] = pipeline
        if _logged_pipeline_key != key:
            _logged_pipeline_key = key
            model = pipeline.model
            device = getattr(model, "device", None) if model is not None else None
            weights = _weights_filename(repo_id)
            print(
                "[TTS] Kokoro pipeline ready: "
                f"huggingface_repo={repo_id} "
                f"checkpoint={weights} "
                f"lang_code={pipeline.lang_code} "
                f"torch_device={device!s}",
                flush=True,
            )
        return pipeline


def prewarm_kokoro_at_startup() -> None:
    """
    Load Kokoro's ``KModel`` and the configured voice into memory while the app is starting.

    Without this, the first double-clap still uses the same cached files from disk, but **PyTorch
    has to build the graph and load weights into RAM** on first use, which is what feels slow.
    After preload, first speech only runs G2P + inference on text that is already "warm" for the
    rest of the stack (and the HF cache on disk is unchanged—you are not re-downloading each time).
    """
    if sys.version_info >= _KOKORO_UNSUPPORTED_PY:
        return
    raw = os.environ.get("JARVIS_KOKORO_NO_PREWARM", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return
    _load_dotenv()
    lang, voice, repo_id = _kokoro_env()
    print(
        "[TTS] Preloading Kokoro into RAM (model + voice; first announcement is faster)…",
        flush=True,
    )
    try:
        pipeline = _get_pipeline(lang, repo_id)
    except ImportError:
        print(
            "[TTS] Kokoro preload skipped (not installed for this interpreter).",
            file=sys.stderr,
            flush=True,
        )
        return
    except Exception as e:
        print(
            f"[TTS] Kokoro preload failed (will retry on first speech): {e}",
            file=sys.stderr,
            flush=True,
        )
        return
    try:
        pipeline.load_voice(voice)
    except Exception as e:
        print(
            f"[TTS] Kokoro voice preload failed (will retry on first speech): {e}",
            file=sys.stderr,
            flush=True,
        )
        return

    deep = os.environ.get("JARVIS_KOKORO_DEEP_PREWARM", "").strip().lower()
    if deep in ("1", "true", "yes", "on"):
        try:
            print("[TTS] Kokoro deep prewarm (one short synthesis)…", flush=True)
            for _ in pipeline("Hello.", voice=voice, speed=1.0, split_pattern=None):
                break
            print("[TTS] Kokoro deep prewarm done.", flush=True)
        except Exception as e:
            print(f"[TTS] Kokoro deep prewarm failed: {e}", file=sys.stderr, flush=True)

    print("[TTS] Kokoro preload finished.", flush=True)


def _result_audio_to_mono_numpy(audio: Any) -> np.ndarray:
    if audio is None:
        return np.array([], dtype=np.float32)
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().float().numpy()
    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    return a


def kokoro_synthesize_to_file(text: str, *, speed: float = 1.0) -> "Path | None":
    """
    Synthesize *text* with Kokoro and write it to a temp WAV.
    Returns the Path on success, None on any failure.
    The caller is responsible for deleting the file.
    Used by the streaming pipeline so synthesis and playback can overlap.
    """
    if sys.version_info >= _KOKORO_UNSUPPORTED_PY:
        return None
    if not (text or "").strip():
        return None
    _load_dotenv()
    try:
        import soundfile as sf
    except ImportError:
        return None
    lang, voice, repo_id = _kokoro_env()
    try:
        pipeline = _get_pipeline(lang, repo_id)
    except Exception:
        return None
    chunks: list[np.ndarray] = []
    tts_speed = max(0.5, min(2.0, float(speed)))
    try:
        for result in pipeline(text, voice=voice, speed=tts_speed, split_pattern=r"\n+"):
            chunks.append(_result_audio_to_mono_numpy(result.audio))
    except Exception:
        return None
    if not chunks or all(c.size == 0 for c in chunks):
        return None
    waveform = np.concatenate([c for c in chunks if c.size > 0])
    peak = float(np.abs(waveform).max())
    if peak > 0.01:
        waveform = (waveform / peak * 0.92).astype(np.float32)
    try:
        fd, name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        tmp = Path(name)
        sf.write(str(tmp), waveform, _SAMPLE_RATE_HZ)
        return tmp
    except Exception:
        return None


def speak_weather_kokoro(
    text: str,
    *,
    output_volume: float = 1.0,
    spotify_duck_volume: int | None = None,
    spotify_duck_halve: bool = False,
    quiet: bool = False,
    say_fallback: bool = True,
    speed: float = 1.0,
    live_volume: bool = False,
) -> None:
    if not (text or "").strip():
        return
    _load_dotenv()
    vol = max(0.0, min(3.0, float(output_volume)))
    duck = (
        None
        if spotify_duck_volume is None
        else max(0, min(100, int(spotify_duck_volume)))
    )
    lang, voice, repo_id = _kokoro_env()

    if sys.version_info >= _KOKORO_UNSUPPORTED_PY:
        print(
            "[TTS] Kokoro is not available on this Python "
            f"({sys.version_info.major}.{sys.version_info.minor}); "
            "use Python 3.12 (see .python-version) or --tts-backend openai. Falling back to say.",
            file=sys.stderr,
            flush=True,
        )
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            _say(text, output_volume=vol)
        return

    if not quiet:
        print(
            "[TTS] synthesizing with Kokoro: "
            f"huggingface_repo={repo_id} "
            f"lang_code={lang} "
            f"voice={voice} "
            f"sample_rate_hz={_SAMPLE_RATE_HZ}",
            flush=True,
        )

    try:
        import soundfile as sf
    except ImportError:
        print("Kokoro TTS needs soundfile. pip install soundfile", file=sys.stderr)
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            _say(text, output_volume=vol)
        return

    try:
        pipeline = _get_pipeline(lang, repo_id)
    except ImportError as e:
        print(
            "[TTS] Kokoro is not installed for this interpreter. "
            f"({e!s}) With Python 3.12: {sys.executable} -m pip install -r requirements.txt. "
            "Falling back to say.",
            file=sys.stderr,
            flush=True,
        )
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            _say(text, output_volume=vol)
        return
    except Exception as e:
        print(f"Kokoro init failed: {e}", file=sys.stderr)
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            _say(text, output_volume=vol)
        return

    chunks: list[np.ndarray] = []
    tts_speed = max(0.5, min(2.0, float(speed)))
    try:
        for result in pipeline(
            text,
            voice=voice,
            speed=tts_speed,
            split_pattern=r"\n+",
        ):
            chunks.append(_result_audio_to_mono_numpy(result.audio))
    except Exception as e:
        print(f"Kokoro synthesis failed: {e}", file=sys.stderr)
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            _say(text, output_volume=vol)
        return

    if not chunks or all(c.size == 0 for c in chunks):
        print("Kokoro returned no audio; falling back to say.", file=sys.stderr)
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            _say(text, output_volume=vol)
        return

    waveform = np.concatenate([c for c in chunks if c.size > 0])
    # Peak-normalize to prevent clipping on loud phonemes.
    peak = float(np.abs(waveform).max())
    if peak > 0.01:
        waveform = (waveform / peak * 0.92).astype(np.float32)
    n_chunks = len(chunks)
    duration_s = float(waveform.shape[0]) / _SAMPLE_RATE_HZ
    if not quiet:
        print(
            f"[TTS] Kokoro generated audio: chunks={n_chunks} duration_s={duration_s:.2f}",
            flush=True,
        )

    if live_volume:
        # Live-volume path: play through sounddevice so the slider takes effect mid-speech.
        # The initial gain comes from ``output_volume``; ``LunaVoiceFollowup`` keeps the live
        # gain in sync via ``set_live_gain`` whenever the slider moves.
        from jarvis.services.live_audio import (
            play_wave_blocking_with_live_gain,
            set_live_gain,
        )

        from jarvis.services.live_audio import was_interrupted

        set_live_gain(vol)
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            ok = play_wave_blocking_with_live_gain(waveform, _SAMPLE_RATE_HZ)
        if ok:
            if not quiet:
                print("[TTS] Kokoro live-volume playback completed.", flush=True)
            return
        if was_interrupted():
            if not quiet:
                print("[TTS] Kokoro playback stopped by barge-in.", flush=True)
            return
        if not quiet:
            print(
                "[TTS] Live-volume playback failed; falling back to afplay.",
                flush=True,
            )

    tmp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        tmp = Path(name)
        sf.write(str(tmp), waveform, _SAMPLE_RATE_HZ)
        if sys.platform != "darwin":
            return
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            r = _play_media_with_volume(tmp, vol)
            if r != 0:
                if say_fallback:
                    print(
                        "[TTS] afplay/ffmpeg returned non-zero; falling back to say.",
                        file=sys.stderr,
                        flush=True,
                    )
                    _say(text, output_volume=vol)
                elif not quiet:
                    print(
                        "[TTS] Kokoro playback ended early (e.g. interrupted).",
                        flush=True,
                    )
            elif not quiet:
                print("[TTS] Kokoro WAV playback completed.", flush=True)
    except Exception as e:
        if say_fallback:
            print(
                f"[TTS] Kokoro write/play failed ({type(e).__name__}: {e}); falling back to macOS say.",
                file=sys.stderr,
                flush=True,
            )
            with _spotify_duck(duck, halve_current=spotify_duck_halve):
                _say(text, output_volume=vol)
        else:
            print(
                f"[TTS] Kokoro write/play failed ({type(e).__name__}: {e}); no say fallback.",
                file=sys.stderr,
                flush=True,
            )
    finally:
        if tmp is not None and tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass
