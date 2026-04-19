"""Play local WAV files via ``afplay`` (macOS)."""

from __future__ import annotations

import random
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def list_wavs_in_dir(directory: Path) -> list[Path]:
    """Sorted list of ``.wav`` files directly inside ``directory`` (non-recursive)."""
    root = directory.expanduser().resolve()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".wav")


def list_wavs_in_dirs(directories: Sequence[Path]) -> list[Path]:
    """Collect ``.wav`` files from each directory (no duplicates by resolved path)."""
    seen: set[Path] = set()
    out: list[Path] = []
    for d in directories:
        for p in list_wavs_in_dir(d):
            r = p.resolve()
            if r not in seen:
                seen.add(r)
                out.append(p)
    return out


def play_random_wav_from_dir(
    directory: Path | Sequence[Path],
    *,
    fallback: Path | None = None,
    extra_fallbacks: Sequence[Path] | None = None,
    exclude_names: Sequence[str] | None = None,
) -> None:
    """Play one random ``.wav`` from ``directory`` or from several folders; else try fallback files.

    ``exclude_names``: basenames (e.g. ``thinking.wav``) never picked for the random wake chime;
    those files may still be played via explicit paths elsewhere.
    """
    if isinstance(directory, Path):
        dirs: tuple[Path, ...] = (directory,)
    else:
        dirs = tuple(directory)
    choices = list_wavs_in_dirs(dirs)
    if exclude_names:
        banned = {n.strip().lower() for n in exclude_names if n.strip()}
        choices = [p for p in choices if p.name.lower() not in banned]
    if choices:
        play_vocals_wav(random.choice(choices))
        return
    for p in (fallback, *(extra_fallbacks or ())):
        if p is None:
            continue
        rp = p.expanduser().resolve()
        if rp.is_file():
            play_vocals_wav(rp)
            return
    tried_dirs = ", ".join(str(d.expanduser().resolve()) for d in dirs)
    tried_files = ", ".join(
        str(p.expanduser().resolve()) for p in (fallback, *(extra_fallbacks or ())) if p is not None
    )
    print(
        f"[Voice] No playable .wav for LUNA. Put ``.wav`` files in jarvis/audio_assets/ (or set "
        f"--luna-audio-dir).\n  Folders checked: {tried_dirs}\n  Files tried: {tried_files}",
        file=sys.stderr,
        flush=True,
    )


def stop_spoken_output_macos() -> None:
    """Stop active TTS (afplay greeting WAV + sounddevice live-volume stream). Used for LUNA barge-in."""
    if sys.platform != "darwin":
        return
    try:
        from jarvis.services.live_audio import stop_live_playback

        stop_live_playback()
    except Exception:
        pass
    subprocess.run(
        ["killall", "afplay"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def play_vocals_wav(path: Path) -> None:
    if sys.platform != "darwin":
        return
    if not path.is_file():
        return
    subprocess.Popen(
        ["afplay", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def play_vocals_wav_blocking(path: Path) -> None:
    if sys.platform != "darwin":
        return
    if not path.is_file():
        return
    subprocess.run(
        ["afplay", str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
