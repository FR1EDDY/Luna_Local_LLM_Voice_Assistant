"""Microphone stream and double-clap onset detection."""

from __future__ import annotations

import math
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    print("pip install -r requirements.txt", file=sys.stderr)
    raise

from jarvis import config
from jarvis.audio import rms

if TYPE_CHECKING:
    from jarvis.luna.chat import LunaVoiceFollowup
    from jarvis.wake_word import WakeWordDetector


@dataclass
class ClapState:
    last_onset_time: float | None = None
    in_loud: bool = False
    cooldown_until: float = 0.0
    welcome_used: bool = False


@dataclass
class WakeState:
    cooldown_until: float = 0.0



def run_double_clap_listener(
    *,
    threshold: float,
    device: int | None,
    verbose: bool,
    on_double_clap: Callable[[], None],
    wake_detector: "WakeWordDetector | None" = None,
    on_wake_word: Callable[[], None] | None = None,
    luna_chat: "LunaVoiceFollowup | None" = None,
    wake_cooldown_s: float | None = None,
    ui_level_callback: Callable[[float], None] | None = None,
) -> None:
    """Block until Ctrl+C; invoke ``on_double_clap`` once per process (see ``ClapState.welcome_used``).

    If ``wake_detector`` and ``on_wake_word`` are set, the same mic stream is also scanned for the
    wake phrase (see ``jarvis.config.WAKE_WORD_PHRASE``).
    """
    block_samples = max(1, int(config.SAMPLE_RATE * config.BLOCK_DURATION_S))
    state = ClapState()
    wake_state = WakeState()
    wake_cd = float(config.WAKE_WORD_COOLDOWN_S if wake_cooldown_s is None else wake_cooldown_s)
    _last_level_push_t: float = 0.0
    use_wake = wake_detector is not None and (
        on_wake_word is not None or luna_chat is not None
    )

    def callback(indata, frames, t, status) -> None:  # type: ignore[no-untyped-def]
        nonlocal _last_level_push_t
        if status:
            print(status, file=sys.stderr)
        now = time.monotonic()

        mono = indata[:, 0] if indata.ndim > 1 else indata.reshape(-1)

        if ui_level_callback is not None and (now - _last_level_push_t) >= 0.08:
            _last_level_push_t = now
            try:
                raw = rms(mono)
                db = 20.0 * math.log10(max(raw, 1e-9))
                # Map -60 dBFS→0 .. 0 dBFS→0.06 so the JS (level/0.06) gives a full 0-1 range
                ui_level_callback(float(max(0.0, (db + 60.0) / 60.0) * 0.06))
            except Exception:
                pass

        if luna_chat is not None and not luna_chat.idle:
            if use_wake and wake_detector is not None:
                try:
                    wake_t, stop_t = wake_detector.process_block(mono)
                    if (wake_t or stop_t) and now >= wake_state.cooldown_until:
                        wake_state.cooldown_until = now + wake_cd
                        wake_detector.reset_after_wake()
                        print(f"{config.WAKE_WORD_PHRASE.upper()} (barge-in).", flush=True)
                        luna_chat.barge_in_luna(now)
                except Exception as ex:  # noqa: BLE001
                    print(f"Wake word error: {ex}", file=sys.stderr)
            luna_chat.feed_audio_block(mono, now)
            return

        if use_wake:
            try:
                wake_t, _stop_t = wake_detector.process_block(mono)
                if wake_t and now >= wake_state.cooldown_until:
                    wake_state.cooldown_until = now + wake_cd
                    wake_detector.reset_after_wake()
                    print(f"Wake word: {config.WAKE_WORD_PHRASE.upper()}.", flush=True)
                    if luna_chat is not None:
                        luna_chat.start_after_wake(now)
                    elif on_wake_word is not None:
                        threading.Thread(target=on_wake_word, daemon=True).start()
            except Exception as ex:  # noqa: BLE001 — avoid killing the audio callback thread
                print(f"Wake word error: {ex}", file=sys.stderr)

        if now < state.cooldown_until:
            return

        level = rms(mono)
        loud = level >= threshold

        if verbose and loud:
            print(f"rms={level:.4f}", flush=True)

        if loud and not state.in_loud:
            if state.last_onset_time is not None:
                gap_ms = (now - state.last_onset_time) * 1000.0
                if gap_ms < config.MIN_GAP_MS:
                    pass
                elif config.MIN_GAP_MS <= gap_ms <= config.MAX_GAP_MS:
                    if state.welcome_used:
                        if verbose:
                            print(
                                "Double clap ignored (welcome already used this session).",
                                flush=True,
                            )
                        state.last_onset_time = None
                        state.cooldown_until = now + config.COOLDOWN_S
                    else:
                        state.welcome_used = True
                        print("Double clap.", flush=True)
                        state.last_onset_time = None
                        state.cooldown_until = now + config.COOLDOWN_S
                        threading.Thread(target=on_double_clap, daemon=True).start()
                else:
                    state.last_onset_time = now
            else:
                state.last_onset_time = now
            state.in_loud = True
        elif not loud:
            state.in_loud = False

    if use_wake:
        print(
            f"Listening (double clap + wake word {config.WAKE_WORD_PHRASE.upper()}). Ctrl+C to stop.",
            flush=True,
        )
    else:
        print("Listening. Ctrl+C to stop.", flush=True)

    try:
        with sd.InputStream(
            device=device,
            channels=1,
            samplerate=config.SAMPLE_RATE,
            blocksize=block_samples,
            callback=callback,
        ):
            while True:
                time.sleep(0.25)
    except KeyboardInterrupt:
        print("Stopped.", flush=True)
