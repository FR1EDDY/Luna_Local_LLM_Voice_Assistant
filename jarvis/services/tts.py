"""OpenAI speech → temp MP3 → ``afplay`` (or ``ffmpeg`` amplify when volume > 100%)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from jarvis import config
from jarvis.paths import PROJECT_ROOT

_VOICE = "nova"
_MODEL = "tts-1"


def _load_dotenv() -> None:
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


def _say(text: str, *, output_volume: float = 1.0) -> None:
    """Speak on macOS. ``output_volume`` 0–1 scales ``afplay``; >1 needs ``ffmpeg``."""
    if sys.platform != "darwin":
        return
    v = max(0.0, min(3.0, float(output_volume)))
    if v <= 0.0:
        return
    if 0.999 <= v <= 1.0 + 1e-9:
        subprocess.run(["say", text], check=False, capture_output=True, text=True)
        return
    tmp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(suffix=".aiff")
        os.close(fd)
        tmp = Path(name)
        subprocess.run(
            ["say", "-o", str(tmp), text],
            check=False,
            capture_output=True,
            text=True,
        )
        if tmp.is_file() and tmp.stat().st_size > 0:
            _play_media_with_volume(tmp, v)
        else:
            subprocess.run(["say", text], check=False, capture_output=True, text=True)
    finally:
        if tmp is not None and tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass


def _afplay_cmd(path: Path, output_volume: float) -> list[str]:
    v = max(0.0, min(1.0, float(output_volume)))
    cmd: list[str] = ["afplay"]
    if v < 0.999:
        cmd.extend(["-v", str(v)])
    cmd.append(str(path))
    return cmd


def _play_media_with_volume(path: Path, gain: float) -> int:
    """
    Play an audio file. ``gain`` 0–1 maps to ``afplay -v``; above 1 uses ``ffmpeg`` then ``afplay``.
    Returns subprocess return code (0 = ok).
    """
    gain = max(0.0, min(3.0, float(gain)))
    if gain <= 0.0:
        return 0
    if gain <= 1.0 + 1e-9:
        r = subprocess.run(
            _afplay_cmd(path, gain),
            check=False,
            capture_output=True,
            text=True,
        )
        return int(r.returncode)

    if not shutil.which("ffmpeg"):
        print(
            "Install ffmpeg and ensure it is in PATH for announcement volumes above 100%. "
            "Playing at 100%.",
            file=sys.stderr,
        )
        r = subprocess.run(
            _afplay_cmd(path, 1.0),
            check=False,
            capture_output=True,
            text=True,
        )
        return int(r.returncode)

    fd, out_name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    out_wav = Path(out_name)
    try:
        r = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-af",
                f"volume={gain:.4f}",
                str(out_wav),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            if err:
                print(f"ffmpeg amplify failed: {err}", file=sys.stderr)
            r2 = subprocess.run(
                _afplay_cmd(path, 1.0),
                check=False,
                capture_output=True,
                text=True,
            )
            return int(r2.returncode)
        r3 = subprocess.run(
            ["afplay", str(out_wav)],
            check=False,
            capture_output=True,
            text=True,
        )
        return int(r3.returncode)
    finally:
        try:
            out_wav.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _spotify_duck(duck_level: int | None, *, halve_current: bool = False):
    """Lower Spotify in-app volume for wrapped playback, then restore.

    * ``duck_level``: absolute 0–100 (legacy weather briefing).
    * ``halve_current``: set volume to half of whatever it is now (Luna dialogue).
    """
    if sys.platform != "darwin":
        yield
        return
    if duck_level is None and not halve_current:
        yield
        return
    from jarvis.integrations.spotify import get_sound_volume, set_sound_volume

    prev = get_sound_volume()
    if prev is None:
        yield
        return
    if halve_current:
        target = max(0, prev // 2)
    else:
        target = max(0, min(100, int(duck_level)))
    set_sound_volume(target)
    try:
        yield
    finally:
        set_sound_volume(prev)


def log_tts_startup_summary(tts_backend: str) -> None:
    """Print which TTS stack is active (model / voice / repo) once at process start."""
    _load_dotenv()
    b = (tts_backend or "").strip().lower()
    if b == "kokoro":
        lang = (os.environ.get("JARVIS_KOKORO_LANG") or config.KOKORO_LANG_CODE).strip()
        voice = (os.environ.get("JARVIS_KOKORO_VOICE") or config.KOKORO_VOICE).strip()
        repo = (os.environ.get("JARVIS_KOKORO_REPO") or config.KOKORO_REPO_ID).strip()
        print(
            "[TTS] configured backend=kokoro "
            f"huggingface_repo={repo} "
            f"planned_lang_code={lang} "
            f"planned_voice={voice} "
            "(env: JARVIS_KOKORO_LANG, JARVIS_KOKORO_VOICE, JARVIS_KOKORO_REPO)",
            flush=True,
        )
        if sys.version_info >= (3, 13):
            maj, min_ = config.RECOMMENDED_PYTHON
            print(
                f"[TTS] warning: default Kokoro needs Python {maj}.{min_} (or 3.10–3.11); "
                "on 3.13+ use a 3.12 conda env or pass --tts-backend openai.",
                file=sys.stderr,
                flush=True,
            )
    else:
        print(
            "[TTS] configured backend=openai "
            f"openai_model={_MODEL} openai_voice={_VOICE} "
            "(needs OPENAI_API_KEY; otherwise macOS say)",
            flush=True,
        )


_DRY_RUN: bool = False


def set_dry_run(enabled: bool) -> None:
    global _DRY_RUN
    _DRY_RUN = bool(enabled)


def speak_weather(
    text: str,
    *,
    tts_backend: str,
    output_volume: float = 1.0,
    spotify_duck_volume: int | None = None,
    spotify_duck_halve: bool = False,
    tts_quiet: bool = False,
    say_fallback: bool = True,
    speed: float = 1.0,
    live_volume: bool = False,
) -> None:
    if _DRY_RUN:
        print(f"[DRY-RUN] speak: {text!r}", flush=True)
        return
    b = (tts_backend or "").strip().lower()
    if b == "kokoro":
        from jarvis.services.tts_kokoro import speak_weather_kokoro

        speak_weather_kokoro(
            text,
            output_volume=output_volume,
            spotify_duck_volume=spotify_duck_volume,
            spotify_duck_halve=spotify_duck_halve,
            quiet=tts_quiet,
            say_fallback=say_fallback,
            speed=speed,
            live_volume=live_volume,
        )
        return
    speak_weather_openai(
        text,
        output_volume=output_volume,
        spotify_duck_volume=spotify_duck_volume,
        spotify_duck_halve=spotify_duck_halve,
    )


def speak_weather_openai(
    text: str,
    *,
    output_volume: float = 1.0,
    spotify_duck_volume: int | None = None,
    spotify_duck_halve: bool = False,
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

    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            _say(text, output_volume=vol)
        return
    try:
        from openai import OpenAI
    except ImportError:
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            _say(text, output_volume=vol)
        return

    tmp: Path | None = None
    try:
        response = OpenAI().audio.speech.create(
            model=_MODEL, voice=_VOICE, input=text
        )
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(response.content)
            tmp = Path(f.name)
        if sys.platform != "darwin":
            return
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            r = _play_media_with_volume(tmp, vol)
            if r != 0:
                _say(text, output_volume=vol)
    except Exception as e:
        print(e, file=sys.stderr)
        with _spotify_duck(duck, halve_current=spotify_duck_halve):
            _say(text, output_volume=vol)
    finally:
        if tmp is not None and tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass
