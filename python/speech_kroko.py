"""Offline streaming speech recognition with Kroko (sherpa-onnx) on the board's CPU.

Drop-in alternative to speech.py (Moonshine), chosen in main.py (ASR = "kroko").

Kroko is a streaming Zipformer transducer: every 0.1 s of audio is processed
once and the text grows word by word, instead of re-decoding the whole line
on every update like Moonshine. It writes punctuation and capitals itself
(spaCy needs them to find names). Lines end at pauses in the talk.

tools/eval, 24 test conversations with restaurant noise (laptop, results in
tools/eval/results/2026-11-13-asr-comparison.md):

    WER clean / noisy   key word top-5 ok   compute per second of audio
    Moonshine Tiny  0.31 / 0.47   0.62   0.156 s
    Kroko           0.04 / 0.19   0.84   0.024 s (2 threads)

Model: Banafo Kroko ASR community model, CC-BY-SA 4.0
(https://huggingface.co/Banafo/Kroko-ASR), packaged for sherpa-onnx.

    stt = SpeechToText(on_line=print_line)   # loads (first run: downloads) the model
    stt.run(mic.blocks())                    # runs while the microphone delivers audio
    stt.listen_for(["Mary", "Grandma"])      # names that should come out right
"""

import tarfile
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import sherpa_onnx

from speech import CATCH_UP_TO, MAX_BACKLOG, _AudioBuffer, _SpeedLog

MODEL_NAME = "sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_NAME}.tar.bz2"
# Downloaded on the first start; deploy.sh keeps this folder on the board.
MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / MODEL_NAME
RATE = 16000
THREADS = 2  # tools/eval: faster than 4 on small chunks, and leaves cores for key words and screen
END_PAUSE = 0.5  # seconds of silence that end a line ...
MAX_LINE = 30.0  # ... (no forced cut after a fixed length: that splits words, "Kyot | o")
# Names to listen for (listen_for) are sherpa-onnx hotwords: during beam search,
# paths that spell one of them get this bonus per token. Needs beam search instead of
# greedy decoding; the encoder dominates the cost, so the beam adds little.
HOTWORD_SCORE = 1.5
BEAM_PATHS = 4

LineCallback = Callable[[int, str, bool], None]


class SpeechToText:
    def __init__(self, on_line: LineCallback):
        _download_model()
        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(MODEL_DIR / "tokens.txt"), encoder=str(MODEL_DIR / "encoder.onnx"),
            decoder=str(MODEL_DIR / "decoder.onnx"), joiner=str(MODEL_DIR / "joiner.onnx"),
            num_threads=THREADS, sample_rate=RATE, feature_dim=80,
            decoding_method="modified_beam_search", max_active_paths=BEAM_PATHS,
            hotwords_score=HOTWORD_SCORE, modeling_unit="bpe", bpe_vocab=str(_bpe_vocab()),
            enable_endpoint_detection=True, rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=END_PAUSE, rule3_min_utterance_length=MAX_LINE)
        self._on_line = on_line
        self._line_id = 0
        self._terms: list[str] = []  # names the current stream listens for ...
        self._wanted_terms: list[str] = []  # ... and the ones it should
        print(f"speech: Kroko (sherpa-onnx {sherpa_onnx.__version__}), {THREADS} threads")

    def listen_for(self, terms: list[str]) -> None:
        """Favor these words where the audio is unclear: the names of the person at
        the screen. Callable from any thread; takes effect at the next pause."""
        self._wanted_terms = list(terms)

    def _new_stream(self):
        self._terms = self._wanted_terms
        if self._terms:
            print(f"speech: listening for {', '.join(self._terms)}")
            return self._recognizer.create_stream(hotwords="/".join(self._terms))
        return self._recognizer.create_stream()

    def run(self, blocks: Iterator[np.ndarray]) -> None:
        """Transcribe mono int16 16 kHz sample blocks until they end or fail."""
        audio = _AudioBuffer(int(MAX_BACKLOG * RATE), int(CATCH_UP_TO * RATE))
        threading.Thread(target=audio.fill_from, args=(blocks,), daemon=True).start()
        stream = self._new_stream()
        speed = _SpeedLog()
        text, line_samples, last_update_ms = "", 0, 0
        try:
            while (samples := audio.take()) is not None:
                started = time.monotonic()
                stream.accept_waveform(RATE, samples / 32768.0)
                while self._recognizer.is_ready(stream):
                    self._recognizer.decode_stream(stream)
                last_update_ms = round((time.monotonic() - started) * 1000)
                speed.add(len(samples) / RATE, time.monotonic() - started)
                line_samples += len(samples)

                new = self._recognizer.get_result(stream).strip()
                if new != text:
                    text = new
                    if text:
                        self._on_line(self._line_id, text, False)
                if self._recognizer.is_endpoint(stream):
                    if text:  # same log line as speech.py: tools/eval reads it
                        print(f"📝 {text}  [{line_samples / RATE:.1f}s of speech, "
                              f"last update took {last_update_ms} ms]")
                        self._on_line(self._line_id, text, True)
                        self._line_id += 1
                    self._recognizer.reset(stream)
                    text, line_samples = "", 0
                    if self._wanted_terms != self._terms:  # in a pause: new names from now on
                        stream = self._new_stream()
        finally:
            audio.close()


def _bpe_vocab() -> Path:
    """sherpa-onnx turns hotwords into model tokens with a SentencePiece vocabulary,
    which the Kroko package lacks. Made from tokens.txt: earlier (more frequent)
    pieces score higher, so names are split into few, long pieces."""
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
    if (MODEL_DIR / "encoder.onnx").exists():
        return
    print(f"speech: downloading {MODEL_NAME} (57 MB, once)")
    MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
        with tarfile.open(fileobj=response, mode="r|bz2") as archive:
            archive.extractall(MODEL_DIR.parent, filter="data")
