"""Offline streaming speech recognition with Moonshine v2 on the board's CPU.

Moonshine v2 is built for live captions on small devices: its encoder streams
(words appear while someone is still talking) and a built-in voice activity
detector cuts the talk into lines, one per utterance. The Small model makes half
the errors of the previous Whistle model on meeting-style conversation
(AMI: 12.5 % vs 26 % word error rate).

    stt = SpeechToText(on_line=print_line)   # loads (first run: downloads) the model
    stt.run(mic)                             # blocks while the microphone delivers audio

on_line(line_id, text, complete) is called whenever the text of a line changes;
a line keeps its id while it is revised and stops changing once complete.
"""

import threading
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np
from moonshine_voice import Error as RecognitionError
from moonshine_voice import (
    LineCompleted,
    LineTextChanged,
    ModelArch,
    Transcriber,
    get_model_for_language,
)

# Small: best accuracy the board's 4 Cortex-A53 cores keep up with in real time.
# If captions lag far behind, use ModelArch.TINY_STREAMING (faster, ~1.5x the errors).
MODEL_ARCH = ModelArch.SMALL_STREAMING
# Downloaded on the first start; deploy.sh keeps this folder on the board.
MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
RATE = 16000
UPDATE_INTERVAL = 0.3  # seconds between caption updates (Moonshine stretches it when busy)
MAX_BACKLOG = 3.0  # seconds; if recognition falls further behind, the oldest audio is dropped

LineCallback = Callable[[int, str, bool], None]


class SpeechToText:
    def __init__(self, on_line: LineCallback):
        model_path, model_arch = get_model_for_language(
            "en", MODEL_ARCH, cache_root=MODEL_DIR)
        self._transcriber = Transcriber(model_path=model_path, model_arch=model_arch)
        self._on_line = on_line

    def run(self, mic) -> None:
        """Transcribe the (started) microphone until its stream ends or fails."""
        audio = _AudioBuffer(int(MAX_BACKLOG * RATE))
        threading.Thread(target=audio.fill_from, args=(mic,), daemon=True).start()

        stream = self._transcriber.create_stream(update_interval=UPDATE_INTERVAL)
        stream.add_listener(self._on_event)
        stream.start()
        try:
            while (samples := audio.take()) is not None:
                stream.add_audio((samples / 32768.0).tolist(), RATE)
        finally:
            stream.stop()
            stream.close()
            audio.close()

    def _on_event(self, event) -> None:
        # A line that changes as it completes gets both events: report it once.
        if (isinstance(event, LineCompleted) or
                isinstance(event, LineTextChanged) and not event.line.is_complete):
            line = event.line
            self._on_line(line.line_id, line.text.strip(), line.is_complete)
        elif isinstance(event, RecognitionError):
            print(f"Speech recognition error: {event.error!r}")


class _AudioBuffer:
    """Microphone samples on their way from the reader thread to the recognizer.

    The recognizer works in bursts and can stall (model warm-up, CPU spikes); the
    reader must never block meanwhile. Audio older than `max_samples` is dropped
    so the captions catch up with the table instead of lagging ever further.
    """

    def __init__(self, max_samples: int):
        self._max = max_samples
        self._blocks = deque()
        self._size = 0
        self._closed = False
        self._ready = threading.Condition()

    def fill_from(self, mic) -> None:
        try:
            for block in mic.stream():
                if self._closed:
                    return
                self._put(np.asarray(block, dtype=np.int16).reshape(-1))
        except Exception as e:
            print(f"Microphone error: {e!r}")
        finally:
            self.close()

    def take(self) -> np.ndarray | None:
        """All samples gathered so far (waits for some), or None once closed."""
        with self._ready:
            self._ready.wait_for(lambda: self._blocks or self._closed)
            if not self._blocks:
                return None
            samples = np.concatenate(self._blocks).astype(np.float32)
            self._blocks.clear()
            self._size = 0
            return samples

    def close(self) -> None:
        with self._ready:
            self._closed = True
            self._ready.notify_all()

    def _put(self, block: np.ndarray) -> None:
        with self._ready:
            self._blocks.append(block)
            self._size += len(block)
            dropped = 0
            while self._size > self._max and len(self._blocks) > 1:
                old = self._blocks.popleft()
                self._size -= len(old)
                dropped += len(old)
            if dropped:
                print(f"(recognition behind, dropped {dropped / RATE:.1f}s of audio)")
            self._ready.notify()
