"""Compare speech recognizers offline on the board: accuracy, CPU cost, key words.

Runs inside the app's Docker image (Moonshine + spaCy from the app's venv),
sherpa-onnx from /bench/pylib:

    docker run --rm -v ~/ArduinoApps/keep_up:/app:ro -v ~/asr_bench:/bench \
      -e PYTHONPATH=/bench/pylib:/app/python:/app/tools/eval \
      --entrypoint /app/.cache/.venv/bin/python ghcr.io/arduino/app-bricks/python-apps-base:0.13.2 \
      /app/tools/eval/bench_asr.py [model ...]

Every file of /bench/audio (made by tools/eval, see refs.json) is streamed in
0.1 s chunks like a microphone would deliver it. Deterministic: same input, same
output - unlike live runs on the board, whose timing varies.

Per model: WER (clean / noisy), CPU seconds per second of audio (all threads;
must stay well below 1, the board also runs keywords and the screen), and key
word recall / precision with the app's own keywords.py on the recognized lines.
Results: /bench/results/<model>.json
"""

import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

BENCH = Path(os.environ.get("BENCH", "/bench"))
AUDIO = BENCH / "audio"
MODELS = BENCH / "models"
MOONSHINE_CACHE = Path(os.environ.get("MOONSHINE_CACHE", "/app/models"))
RATE = 16000
CHUNK = 1600  # 0.1 s
TAIL = 2.0  # silence after the audio so the last line completes
THREADS = int(os.environ.get("THREADS", "4"))
if os.environ.get("PIN_CPUS"):  # e.g. "0-3": run on as many cores as the board has
    lo, hi = map(int, os.environ["PIN_CPUS"].split("-"))
    os.sched_setaffinity(0, range(lo, hi + 1))


# --- recognizers: each yields completed lines for a stream of chunks ----------------------

def moonshine(arch_name: str):
    from moonshine_voice import LineCompleted, ModelArch, Transcriber, get_model_for_language

    arch = getattr(ModelArch, arch_name)
    path, arch = get_model_for_language("en", arch, cache_root=MOONSHINE_CACHE)
    transcriber = Transcriber(model_path=path, model_arch=arch, options={
        "transcription_interval": 0.5, "vad_max_segment_duration": 4.0, "return_audio_data": "false"})

    def run(chunks):
        lines = []
        stream = transcriber.create_stream(update_interval=0.5)
        stream.add_listener(lambda e: lines.append(e.line.text.strip()) if isinstance(e, LineCompleted) else None)
        stream.start()
        for c in chunks:
            stream.add_audio(c.tolist(), RATE)
        stream.stop()
        stream.close()
        return [l for l in lines if l]
    return run


def _sherpa_run(recognizer, punct=None, hotwords=""):
    def run(chunks):
        lines = []
        stream = recognizer.create_stream(hotwords=hotwords) if hotwords else recognizer.create_stream()
        last = ""

        def finish():
            text = recognizer.get_result(stream).strip()
            if text:
                lines.append(punct.add_punctuation_with_case(text.lower()) if punct else text)
            recognizer.reset(stream)

        for c in chunks:
            stream.accept_waveform(RATE, c)
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            if recognizer.is_endpoint(stream):
                finish()
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        finish()
        return lines
    return run


def _punct():
    import sherpa_onnx
    d = MODELS / "sherpa-onnx-online-punct-en-2024-08-06"
    model = next(d.glob("*.int8.onnx"), None) or next(d.glob("*.onnx"))
    return sherpa_onnx.OnlinePunctuation(sherpa_onnx.OnlinePunctuationConfig(
        model_config=sherpa_onnx.OnlinePunctuationModelConfig(
            cnn_bilstm=str(model), bpe_vocab=str(d / "bpe.vocab"), num_threads=1)))


ENDPOINT = dict(enable_endpoint_detection=True, rule1_min_trailing_silence=2.4,
                rule2_min_trailing_silence=0.8, rule3_min_utterance_length=8.0)


def zipformer(dirname: str, punctuate: bool, hotwords: str = "", **endpoint):
    import sherpa_onnx
    d = MODELS / dirname

    def pick(prefix):
        files = sorted(d.glob(f"{prefix}*.onnx"))
        return str(next((f for f in files if "int8" in f.name), files[0]))
    rec = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(d / "tokens.txt"), encoder=pick("encoder"), decoder=pick("decoder"),
        joiner=pick("joiner"), num_threads=THREADS, sample_rate=RATE, feature_dim=80,
        **{"decoding_method": "greedy_search", **ENDPOINT, **endpoint})
    return _sherpa_run(rec, _punct() if punctuate else None, hotwords)


def nemo_ctc(dirname: str, punctuate: bool):
    import sherpa_onnx
    d = MODELS / dirname
    rec = sherpa_onnx.OnlineRecognizer.from_nemo_ctc(
        tokens=str(d / "tokens.txt"), model=str(next(d.glob("*.onnx"))),
        num_threads=THREADS, sample_rate=RATE, feature_dim=80, **ENDPOINT)
    return _sherpa_run(rec, _punct() if punctuate else None)


def with_denoiser(recognize, kind: str):
    """Streaming noise suppression in front of the recognizer."""
    import sherpa_onnx
    d = MODELS / "denoise"
    model = sherpa_onnx.OfflineSpeechDenoiserModelConfig(num_threads=1)
    if kind == "gtcrn":
        model.gtcrn = sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=str(d / "gtcrn_simple.onnx"))
    else:
        model.dpdfnet = sherpa_onnx.OfflineSpeechDenoiserDpdfNetModelConfig(model=str(d / "dpdfnet2.onnx"))
    denoiser = sherpa_onnx.OnlineSpeechDenoiser(sherpa_onnx.OnlineSpeechDenoiserConfig(model=model))

    def run(chunks):
        def clean():
            for c in chunks:
                out = denoiser.run(c, RATE)
                if len(out.samples):
                    yield np.asarray(out.samples, dtype=np.float32)
            out = denoiser.flush()
            if len(out.samples):
                yield np.asarray(out.samples, dtype=np.float32)
            denoiser.reset()
        return recognize(list(clean()))
    return run


CANDIDATES = {
    "moonshine-tiny": lambda: moonshine("TINY_STREAMING"),
    "moonshine-small": lambda: moonshine("SMALL_STREAMING"),
    "kroko": lambda: zipformer("sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06", False),
    "kroko+punct": lambda: zipformer("sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06", True),
    # lines end at pauses only (no forced cut mid-word after 8 s)
    "kroko-pause05": lambda: zipformer("sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06", False,
                                       rule2_min_trailing_silence=0.5, rule3_min_utterance_length=30.0),
    # as in python/speech_kroko.py: beam search (needed for names), optionally with the
    # names of the test conversations as hotwords (bpe.vocab is written by the app)
    "kroko-beam": lambda: zipformer("sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06", False,
                                    rule2_min_trailing_silence=0.5, rule3_min_utterance_length=30.0,
                                    decoding_method="modified_beam_search", max_active_paths=4),
    "kroko-beam-names": lambda: zipformer(
        "sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06", False, hotwords="Bella/Maria/Peter/Tom/Jonas",
        rule2_min_trailing_silence=0.5, rule3_min_utterance_length=30.0,
        decoding_method="modified_beam_search", max_active_paths=4, hotwords_score=1.5, modeling_unit="bpe",
        bpe_vocab=str(MODELS / "sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06" / "bpe.vocab")),
    "kroko-pause03": lambda: zipformer("sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06", False,
                                       rule2_min_trailing_silence=0.3, rule3_min_utterance_length=30.0),
    "zipformer-20M+punct": lambda: zipformer("sherpa-onnx-streaming-zipformer-en-20M-2023-02-17", True),
    "zipformer-0626+punct": lambda: zipformer("sherpa-onnx-streaming-zipformer-en-2023-06-26", True),
    "nemo-ctc-480+punct": lambda: nemo_ctc("sherpa-onnx-nemo-streaming-fast-conformer-ctc-en-480ms-int8", True),
    "nemo-ctc-80+punct": lambda: nemo_ctc("sherpa-onnx-nemo-streaming-fast-conformer-ctc-en-80ms-int8", True),
    "nemo-rnnt-480+punct": lambda: zipformer("sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-480ms-int8", True),
}


def make(name: str):
    """'model' or 'model@gtcrn' / 'model@dpdfnet' (noise suppression in front)."""
    base, _, denoise = name.partition("@")
    recognize = CANDIDATES[base]()
    return with_denoiser(recognize, denoise) if denoise else recognize


# --- scoring ---------------------------------------------------------------------------------

def main(names):
    import eval as ev  # tools/eval/eval.py: words, stem, wer (standard library only)
    from keywords import keywords  # the app's key word extraction (spaCy)

    refs = json.loads((AUDIO / "refs.json").read_text())
    (BENCH / "results").mkdir(exist_ok=True)
    summary = []
    for name in names or CANDIDATES:
        t0 = time.monotonic()
        try:
            recognize = make(name)
        except Exception as e:
            print(f"{name}: failed to load ({e!r})", flush=True)
            continue
        load_s = time.monotonic() - t0
        rows = []
        for file, ref in refs.items():
            with wave.open(str(AUDIO / f"{file}.wav")) as w:
                audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
            audio = np.concatenate([audio, np.zeros(int(TAIL * RATE), np.float32)])
            chunks = [audio[i:i + CHUNK] for i in range(0, len(audio), CHUNK)]
            cpu0, wall0 = time.process_time(), time.monotonic()
            lines = recognize(chunks)
            cpu, wall = time.process_time() - cpu0, time.monotonic() - wall0

            found = {k.text.lower() for l in lines for k in keywords(l)}
            found_stems = {" ".join(ev.stem(w) for w in ev.words(k)) for k in found}
            ref_stems = {ev.stem(w) for w in ev.words(ref["reference"])}
            expect = [set(g) for g in ref["expect"]]
            hit = [g for g in expect if any(" ".join(ev.stem(w) for w in ev.words(a)) in found_stems for a in g)]
            said = [k for k in found if all(ev.stem(w) in ref_stems for w in ev.words(k))]
            rows.append({
                "file": file, "noisy": "snr" in file,
                "wer": ev.wer(ev.words(ref["reference"]), ev.words(" ".join(lines))),
                "cpu_per_s": cpu / (len(audio) / RATE),
                "busy": wall / (len(audio) / RATE),  # like the app's "speech: busy": wall time per audio second
                "kw_recall": len(hit) / len(expect), "kw_precision": len(said) / max(len(found), 1),
                "lines": lines, "keywords": sorted(found),
            })
        avg = lambda key, noisy=None: round(float(np.mean([r[key] for r in rows if noisy is None or r["noisy"] == noisy])), 3)
        res = {"model": name, "load_s": round(load_s, 1), "wer_clean": avg("wer", False), "wer_noisy": avg("wer", True),
               "cpu_per_s": avg("cpu_per_s"), "busy": avg("busy"), "kw_recall": avg("kw_recall"), "kw_precision": avg("kw_precision"), "rows": rows}
        (BENCH / "results" / f"{name}.json").write_text(json.dumps(res, indent=1))
        summary.append(res)
        print(f"{name:22s} WER clean {res['wer_clean']:.2f} noisy {res['wer_noisy']:.2f}  "
              f"busy {res['busy']:.3f}  CPU {res['cpu_per_s']:.2f} s/s  kw recall {res['kw_recall']:.2f} prec {res['kw_precision']:.2f}  "
              f"(load {res['load_s']}s)", flush=True)
        print(f"   e.g. {rows[4]['lines'][:3]}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
