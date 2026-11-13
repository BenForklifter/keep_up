"""Offline streaming speech recognition with Kroko on sherpa-onnx.

Kroko is a streaming Zipformer transducer: every 0.1 s of audio is decoded once
and the text of the current line grows word by word. It writes punctuation and
capitals itself, which spaCy relies on to find names. A line ends at a pause.

The model (Banafo Kroko ASR community model, CC BY-SA 4.0, 57 MB) is downloaded
on the first start. Of the streaming models tested with restaurant noise, it was
the most accurate, at the lowest CPU load.

    stt = SpeechToText(on_line)           # on_line(line_id, text, complete)
    stt.listen_for(["Mary", "Grandma"])   # names that should come out right
    stt.run(mic.blocks())                 # returns once the audio ends

A line keeps its id while its text grows and is reported a last time with
complete=True when it ends.
"""

import shutil
import tarfile
import threading
import time
import urllib.request
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np
import sherpa_onnx

MODEL_NAME = "sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06"
MODEL_URL = (
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_NAME}.tar.bz2"
)
MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / MODEL_NAME  # kept by deploy.sh
RATE = 16000
THREADS = 2  # faster than 4 on small chunks, and leaves cores to spaCy and the screen
END_PAUSE = 0.5  # seconds of silence that end a line ...
MAX_LINE = 30.0  # ... rather than a fixed length: forced cuts split words ("Kyot | o")
# Names to listen for are hotwords: beam search paths that spell one get this bonus per
# token. Hotwords need beam search instead of greedy decoding, which adds little cost.
HOTWORD_SCORE = 1.5
BEAM_PATHS = 4

# Recognition runs in bursts and can briefly fall behind fast talk; a long backlog lets
# it catch up in the pauses. Beyond it, the oldest audio is dropped in a single cut.
MAX_BACKLOG = 8.0  # seconds
CATCH_UP_TO = 1.0  # seconds
SPEED_LOG_SECONDS = 15.0  # how often the log says whether recognition keeps up

LineCallback = Callable[[int, str, bool], None]


class SpeechToText:
    def __init__(self, on_line: LineCallback) -> None:
        _download_model()
        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(MODEL_DIR / "tokens.txt"),
            encoder=str(MODEL_DIR / "encoder.onnx"),
            decoder=str(MODEL_DIR / "decoder.onnx"),
            joiner=str(MODEL_DIR / "joiner.onnx"),
            num_threads=THREADS,
            sample_rate=RATE,
            feature_dim=80,
            decoding_method="modified_beam_search",
            max_active_paths=BEAM_PATHS,
            hotwords_score=HOTWORD_SCORE,
            modeling_unit="bpe",
            bpe_vocab=str(_bpe_vocab()),
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=END_PAUSE,
            rule3_min_utterance_length=MAX_LINE,
        )
        self._on_line = on_line
        self._line_id = 0
        self._terms: list[str] = []  # names the current stream listens for ...
        self._wanted_terms: list[str] = []  # ... and the ones it should
        print(f"speech: Kroko on sherpa-onnx {sherpa_onnx.__version__}, {THREADS} threads")

    def listen_for(self, terms: list[str]) -> None:
        """Favor these words where the audio is unclear, e.g. the names of the person at
        the screen. Callable from any thread; takes effect at the next pause."""
        self._wanted_terms = list(terms)

    def run(self, blocks: Iterable[np.ndarray]) -> None:
        """Transcribe mono int16 16 kHz sample blocks until they end or fail."""
        audio = _AudioBuffer(int(MAX_BACKLOG * RATE), int(CATCH_UP_TO * RATE))
        threading.Thread(target=audio.fill_from, args=(blocks,), daemon=True).start()
        stream = self._new_stream()
        speed = _SpeedLog()
        text, line_samples, update_ms = "", 0, 0
        try:
            while (samples := audio.take()) is not None:
                started = time.monotonic()
                stream.accept_waveform(RATE, samples / 32768.0)
                while self._recognizer.is_ready(stream):
                    self._recognizer.decode_stream(stream)
                busy = time.monotonic() - started
                update_ms = round(busy * 1000)
                speed.add(len(samples) / RATE, busy)
                line_samples += len(samples)

                new_text = self._recognizer.get_result(stream).strip()
                if new_text != text:
                    text = new_text
                    if text:
                        self._on_line(self._line_id, text, False)
                if self._recognizer.is_endpoint(stream):
                    if text:
                        print(
                            f'speech: heard "{text}"  '
                            f"[{line_samples / RATE:.1f} s, last update {update_ms} ms]"
                        )
                        self._on_line(self._line_id, text, True)
                        self._line_id += 1
                    self._recognizer.reset(stream)
                    text, line_samples = "", 0
                    if self._wanted_terms != self._terms:  # in a pause: switch names
                        stream = self._new_stream()
        finally:
            audio.close()

    def _new_stream(self):
        self._terms = self._wanted_terms
        if not self._terms:
            return self._recognizer.create_stream()
        print(f"speech: listening for {', '.join(self._terms)}")
        return self._recognizer.create_stream(hotwords="/".join(self._terms))


def _bpe_vocab() -> Path:
    """SentencePiece vocabulary that sherpa-onnx needs to turn hotwords into tokens.

    The Kroko package lacks one, so it is made from tokens.txt: earlier (more
    frequent) pieces score higher, so names are split into few, long pieces.
    """
    vocab = MODEL_DIR / "bpe.vocab"
    if not vocab.exists():
        lines = []
        for line in (MODEL_DIR / "tokens.txt").read_text().splitlines():
            piece, _, index = line.rpartition(" ")
            if piece and not piece.startswith("<"):
                lines.append(f"{piece}\t{-int(index)}")
        vocab.write_text("\n".join(lines) + "\n")
    return vocab


def _download_model() -> None:
    """Download and unpack the model once; an interrupted download is never used."""
    if (MODEL_DIR / "encoder.onnx").exists():
        return
    print(f"speech: downloading {MODEL_NAME} (57 MB, once)")
    MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
    partial = MODEL_DIR.parent / f"{MODEL_NAME}.partial"
    shutil.rmtree(partial, ignore_errors=True)
    with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
        with tarfile.open(fileobj=response, mode="r|bz2") as archive:
            archive.extractall(partial, filter="data")
    shutil.rmtree(MODEL_DIR, ignore_errors=True)
    (partial / MODEL_NAME).rename(MODEL_DIR)
    shutil.rmtree(partial)


class _SpeedLog:
    """Logs every SPEED_LOG_SECONDS of audio whether recognition keeps up.

    busy: share of real time spent recognizing; near 100 % the captions fall
    behind. waited: the most audio that piled up while the recognizer was busy.
    """

    def __init__(self) -> None:
        self._audio = self._busy = self._waited = 0.0

    def add(self, audio_seconds: float, busy_seconds: float) -> None:
        self._audio += audio_seconds
        self._busy += busy_seconds
        self._waited = max(self._waited, audio_seconds)
        if self._audio >= SPEED_LOG_SECONDS:
            print(
                f"speech: busy {self._busy / self._audio:.0%} of real time, "
                f"audio waited up to {self._waited:.1f} s"
            )
            self._audio = self._busy = self._waited = 0.0


class _AudioBuffer:
    """Microphone samples on their way from the reader thread to the recognizer.

    The reader must never block while the recognizer is busy. Once more than
    `max_samples` wait, the oldest audio is dropped down to `keep_samples`, so the
    captions catch up with the table instead of lagging ever further.
    """

    def __init__(self, max_samples: int, keep_samples: int) -> None:
        self._max = max_samples
        self._keep = keep_samples
        self._blocks: deque[np.ndarray] = deque()
        self._size = 0
        self._closed = False
        self._ready = threading.Condition()

    def fill_from(self, blocks: Iterable[np.ndarray]) -> None:
        try:
            for block in blocks:
                if self._closed:
                    return
                self._put(block)
        except Exception as e:
            print(f"mic: {e!r}")
        finally:
            self.close()

    def take(self) -> np.ndarray | None:
        """All samples gathered so far as float32 (waits for some), or None once closed."""
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
                print(f"speech: falling behind, dropped {dropped / RATE:.1f} s of audio")
            self._ready.notify()
