"""Sounddevice playback with **live gain** that can be changed mid-speech.

``afplay -v`` and ffmpeg's ``volume`` filter both bake the gain into the file/process at
launch, so the volume slider only takes effect on the *next* utterance. This module plays
a WAV through a sounddevice ``OutputStream`` whose callback multiplies samples by a
shared, thread-safe gain — moving the slider during Luna's reply is reflected immediately.

Used only by Luna's chat replies. The welcome briefing still uses ``afplay``.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np

# Shared, thread-safe live gain (0.0–3.0). Updated by the UI; read every callback tick.
_lock = threading.Lock()
_live_gain: float = 1.0
_stop_evt: Optional[threading.Event] = None
_was_interrupted: bool = False
# Only one Kokoro→sounddevice playback at a time (prevents overlapping OutputStreams after barge-in/stop).
_playback_guard = threading.Lock()


def set_live_gain(gain: float) -> float:
    """Clamp into [0, 1] and store. Returns the value actually applied."""
    g = max(0.0, min(1.0, float(gain)))
    with _lock:
        global _live_gain
        _live_gain = g
    return g


def get_live_gain() -> float:
    with _lock:
        return _live_gain


def stop_live_playback() -> None:
    """Signal the active stream (if any) to stop ASAP. Used for LUNA barge-in."""
    global _was_interrupted
    with _lock:
        evt = _stop_evt
        _was_interrupted = True
    if evt is not None:
        evt.set()


def was_interrupted() -> bool:
    """True if the last playback was explicitly stopped via stop_live_playback (not a device error)."""
    global _was_interrupted
    with _lock:
        result = _was_interrupted
        _was_interrupted = False
    return result


def play_wave_blocking_with_live_gain(
    wave: np.ndarray,
    sample_rate: int,
    *,
    blocksize: int = 1024,
) -> bool:
    """
    Play ``wave`` (mono float32, range [-1, 1]) at the current live gain.

    Blocks until playback ends or ``stop_live_playback`` is called. Returns True on a
    clean finish, False if interrupted or sounddevice was unavailable. The caller can
    fall back to ``afplay`` when False is returned.
    """
    global _stop_evt, _was_interrupted
    try:
        import sounddevice as sd
    except Exception:
        return False

    if wave is None or wave.size == 0:
        return True

    audio = np.asarray(wave, dtype=np.float32).reshape(-1)
    pos = 0
    stop_evt = threading.Event()
    finish_evt = threading.Event()

    # Serialize: overlapping OutputStreams on the default device led to silence after stop/barge-in.
    with _playback_guard:
        with _lock:
            _stop_evt = stop_evt
            _was_interrupted = False

        def callback(outdata, frames, _time_info, status):  # type: ignore[no-untyped-def]
            nonlocal pos
            if stop_evt.is_set():
                outdata.fill(0.0)
                raise sd.CallbackStop
            end = pos + frames
            chunk = audio[pos:end]
            pos = end
            # Apply current live gain, then hard-clip to [-1, 1] (matches ffmpeg behavior).
            gain = get_live_gain()
            if chunk.shape[0] < frames:
                buf = np.zeros(frames, dtype=np.float32)
                if chunk.shape[0] > 0:
                    buf[: chunk.shape[0]] = chunk
                outdata[:, 0] = np.clip(buf * gain, -1.0, 1.0)
                finish_evt.set()
                raise sd.CallbackStop
            outdata[:, 0] = np.clip(chunk * gain, -1.0, 1.0)

        ok_finish = False
        try:
            with sd.OutputStream(
                samplerate=int(sample_rate),
                channels=1,
                dtype="float32",
                blocksize=blocksize,
                callback=callback,
            ):
                # Wait for natural end OR explicit stop.
                while not finish_evt.is_set() and not stop_evt.is_set():
                    finish_evt.wait(timeout=0.05)
            ok_finish = True
        except Exception:
            ok_finish = False
        finally:
            with _lock:
                # Only clear if we still own the slot (avoid a late finally from session A
                # wiping session B's stop_evt registration).
                if _stop_evt is stop_evt:
                    _stop_evt = None

        return ok_finish and not stop_evt.is_set()
