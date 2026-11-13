"""Offline streaming speech recognition with Moonshine v2 on the board's CPU.

Moonshine v2 is built for live captions on small devices: its encoder streams
and lines are decoded while they are still spoken (words appear during the
sentence, not after it), and a built-in voice activity detector cuts the talk
into lines, one per utterance.

Speed matters more than the last bit of accuracy here: every caption update
re-decodes the current line, and Moonshine waits for one update to finish before
starting the next. So the model is the Tiny one and lines are kept short. Word
error rates on meeting talk (AMI): Tiny 19 %, Small 12.5 %, the earlier Whistle 26 %.

    stt = SpeechToText(on_line=print_line)   # loads (first run: downloads) the model
    stt.run(mic.blocks())                    # runs while the microphone delivers audio

on_line(line_id, text, complete) is called whenever the text of a line changes;
a line keeps its id while it is revised and stops changing once complete.

    stt.listen_for(["Mary", "Grandma"])      # names that should come out right
"""

import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
from moonshine_voice import Error as RecognitionError
from moonshine_voice import (
    LineCompleted,
    LineTextChanged,
    ModelArch,
    Transcriber,
    get_model_for_language,
)

# Tiny (34 M parameters) keeps up on the board's 4 Cortex-A53 cores with frequent
# updates; SMALL_STREAMING (123 M) is more accurate but updates only every few seconds.
MODEL_ARCH = ModelArch.TINY_STREAMING
# Downloaded on the first start; deploy.sh keeps this folder on the board.
MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
RATE = 16000
UPDATE_INTERVAL = 0.5  # seconds between caption updates (Moonshine stretches it when busy)
MAX_BACKLOG = 8.0  # seconds; if recognition falls further behind, old audio is dropped ...
# (tools/eval: at 3 s, bursts of fast talk lost 2-4 s per 20 s although recognition
# averages well below real time - a longer buffer lets it catch up in the pauses)
CATCH_UP_TO = 1.0  # ... at once down to this, so words get cut once rather than again and again
SPEED_LOG_SECONDS = 15.0  # how often the log says whether recognition keeps up
# Every update hands back all lines of its stream; renewing the stream in a pause
# keeps that short.
LINES_PER_STREAM = 20
OPTIONS = {
    "transcription_interval": UPDATE_INTERVAL,  # library default 0.5 s would override ours
    "vad_max_segment_duration": 4.0,  # cut lines after 4 s (default 15): cheaper updates
    # Default true: every update would also hand back the audio of every line so
    # far, copied into Python lists - more work the longer the conversation runs.
    "return_audio_data": "false",
}

LineCallback = Callable[[int, str, bool], None]


class SpeechToText:
    def __init__(self, on_line: LineCallback):
        model_path, model_arch = get_model_for_language(
            "en", MODEL_ARCH, cache_root=MODEL_DIR)
        self._transcriber = Transcriber(model_path=model_path, model_arch=model_arch,
                                        options=OPTIONS)
        self._on_line = on_line
        print(f"speech: {MODEL_ARCH.name} model, {_cpus()}")
        self._lines_done = 0  # completed lines in the current stream
        self._speaking = False  # a line is open
        self._terms: list[str] = []  # key terms the recognizer listens for ...
        self._wanted_terms: list[str] = []  # ... and the ones it should

    def listen_for(self, terms: list[str]) -> None:
        """Favor these words where the audio is unclear: the names of the person at
        the screen, which the model would otherwise often hear as everyday words.

        Moonshine key terms: while decoding, tokens that continue one of them get a
        bonus. Callable from any thread; takes effect from the next audio on. The
        bonus can be set with the "keyterm_boost" option (default 2.0; strong boosts
        and long lists garble the other words).
        """
        self._wanted_terms = list(terms)

    def run(self, blocks: Iterator[np.ndarray]) -> None:
        """Transcribe mono int16 16 kHz sample blocks until they end or fail."""
        audio = _AudioBuffer(int(MAX_BACKLOG * RATE), int(CATCH_UP_TO * RATE))
        threading.Thread(target=audio.fill_from, args=(blocks,), daemon=True).start()

        stream = self._open_stream()
        speed = _SpeedLog()
        try:
            while (samples := audio.take()) is not None:
                if self._wanted_terms != self._terms:
                    self._use_terms(self._wanted_terms)
                started = time.monotonic()
                stream.add_audio((samples / 32768.0).tolist(), RATE)
                speed.add(len(samples) / RATE, time.monotonic() - started)
                if self._lines_done >= LINES_PER_STREAM and not self._speaking:
                    stream.stop()
                    stream.close()
                    stream = self._open_stream()
        finally:
            stream.stop()
            stream.close()
            audio.close()

    def _use_terms(self, terms: list[str]) -> None:
        """In the recognition thread, between two pieces of audio."""
        self._terms = terms  # also on failure: tried once, not again with every piece
        try:
            self._transcriber.set_keyterms(terms or None)
            print(f"speech: listening for {', '.join(terms) or 'no names'}")
        except Exception as e:
            print(f"speech: cannot listen for {terms}: {e!r}")

    def _open_stream(self):
        stream = self._transcriber.create_stream(update_interval=UPDATE_INTERVAL)
        stream.add_listener(self._on_event)
        stream.start()
        self._lines_done, self._speaking = 0, False
        return stream

    def _on_event(self, event) -> None:
        # A line that changes as it completes gets both events: report it once.
        if (isinstance(event, LineCompleted) or
                isinstance(event, LineTextChanged) and not event.line.is_complete):
            line = event.line
            self._speaking = not line.is_complete
            if line.is_complete:
                self._lines_done += 1
                print(f"📝 {line.text.strip()}  "
                      f"[{line.duration:.1f}s of speech, last update took {line.last_transcription_latency_ms} ms]")
            self._on_line(line.line_id, line.text.strip(), line.is_complete)
        elif isinstance(event, RecognitionError):
            print(f"Speech recognition error: {event.error!r}")


def _cpus() -> str:
    """How much CPU this process may use (a container can be limited)."""
    cores = len(os.sched_getaffinity(0))
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        limit = "no limit" if quota == "max" else f"limited to {int(quota) / int(period):.1f}"
    except (OSError, ValueError):
        limit = "limit unknown"
    return f"{cores} CPU cores ({limit})"


class _SpeedLog:
    """Logs every SPEED_LOG_SECONDS of audio whether recognition keeps up.

    busy: share of real time spent recognizing; near or above 100 % the captions
    fall behind. waited: the most audio that piled up while the recognizer was
    busy, i.e. how late words reached it.
    """

    def __init__(self):
        self._audio = self._busy = self._waited = 0.0

    def add(self, audio_seconds: float, busy_seconds: float) -> None:
        self._audio += audio_seconds
        self._busy += busy_seconds
        self._waited = max(self._waited, audio_seconds)
        if self._audio >= SPEED_LOG_SECONDS:
            print(f"speech: busy {self._busy / self._audio:.0%} of real time, "
                  f"audio waited up to {self._waited:.1f}s")
            self._audio = self._busy = self._waited = 0.0


class _AudioBuffer:
    """Microphone samples on their way from the reader thread to the recognizer.

    The recognizer works in bursts and can stall (model warm-up, CPU spikes); the
    reader must never block meanwhile. Once more than `max_samples` wait, the
    oldest audio is dropped down to `keep_samples`, so the captions catch up with
    the table instead of lagging ever further - in one cut, as every cut garbles
    the words around it.
    """

    def __init__(self, max_samples: int, keep_samples: int):
        self._max = max_samples
        self._keep = keep_samples
        self._blocks = deque()
        self._size = 0
        self._closed = False
        self._ready = threading.Condition()

    def fill_from(self, blocks: Iterator[np.ndarray]) -> None:
        try:
            for block in blocks:
                if self._closed:
                    return
                self._put(block)
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
            if self._size > self._max:
                dropped = 0
                while self._size > self._keep and len(self._blocks) > 1:
                    old = self._blocks.popleft()
                    self._size -= len(old)
                    dropped += len(old)
                print(f"(recognition behind, dropped {dropped / RATE:.1f}s of audio)")
            self._ready.notify()
