# Speech recognizer comparison (laptop, 2026-11-13)

Board not available, so the candidates ran on a laptop: accuracy carries over
(same model files, same int8 math), speed only relatively — calibrated against
Moonshine Tiny / Small, whose cost on the board is known from live runs.
Numbers: `2026-11-13-asr-comparison.json` (same folder).

## Method

- **Test set**: 6 scripted dinner conversations (`tools/eval/scenarios/`) × 4 conditions
  = 24 files, 455 s: clean, restaurant noise at 15 dB (two different mixes), 10 dB.
  Piper voices (3 speakers at different distances), room reverb, overlaps and
  backchannels, real restaurant noise (DEMAND PRESTO). Made by `tools/eval/eval.py`.
  The reSpeaker's own noise suppression is bypassed: a worst case.
- **Run**: `tools/eval/bench_asr.py` streams every file in 0.1 s chunks into each
  recognizer (deterministic), pinned to 4 cores like the board.
  `tools/eval/bench_keywords.py` runs the app's `keywords.py` on the recognized lines.
- **busy** = wall time per second of audio, like the app's `speech: busy` log.
  Laptop values are noisy (±50 %) — read them as order of magnitude.
- **top-5 ok** = of the 5 strongest key words of a conversation (≈ what the screen
  shows), the share that is expected or fine for that conversation.

## Recognizers

| Model | WER clean | WER 15 dB | WER 10 dB | busy (laptop) | key word recall | key word precision |
|---|---|---|---|---|---|---|
| Moonshine Tiny (app today) | 0.31 | 0.44 | 0.54 | 0.156 | 0.68 | 0.68 |
| Moonshine Small | 0.19 | 0.30 | 0.38 | 0.529 | 0.78 | 0.79 |
| **Kroko** (sherpa-onnx, 4 threads) | 0.06 | 0.16 | 0.28 | 0.057 | 0.86 | 0.83 |
| **Kroko, 2 threads** | 0.06 | 0.15 | 0.29 | **0.024** | 0.86 | 0.83 |
| **Kroko, lines end at 0.5 s pauses** | **0.04** | **0.14** | **0.27** | 0.096 | **0.87** | **0.86** |
| Kroko, lines end at 0.3 s pauses | 0.04 | 0.14 | 0.27 | 0.084 | 0.87 | 0.86 |
| Kroko + punctuation model | 0.06 | 0.16 | 0.28 | 0.066 | 0.86 | 0.83 |
| NeMo FastConformer RNN-T 480 ms + punct. | 0.13 | 0.30 | 0.47 | 0.175 | 0.67 | 0.81 |
| NeMo FastConformer CTC 480 ms + punct. | 0.15 | 0.32 | 0.46 | 0.058 | 0.62 | 0.71 |
| NeMo FastConformer CTC 80 ms + punct. | 0.14 | 0.31 | 0.50 | 0.157 | 0.61 | 0.72 |
| Zipformer en 2023-06-26 + punct. | 0.30 | 0.33 | 0.46 | 0.143 | 0.54 | 0.64 |
| Zipformer en 20M + punct. | 0.32 | 0.39 | 0.55 | 0.069 | 0.48 | 0.54 |

On the board (from live runs): Moonshine Tiny busy ≈ 0.5–1.0 (keeps up, little
headroom), Small ≈ 1.2–2.0 (falls behind, drops audio). Board ≈ 4× laptop, so
Kroko should land around **0.1–0.4** — real time with room to spare.

Kroko beats Moonshine Tiny on **every one of the 24 files**, and Moonshine Small too
at a fraction of its cost. It writes punctuation and capitals itself (an extra
punctuation model adds nothing). Its forced line cut after 8 s split words
("Kyot | o"); ending lines at pauses only fixed that.

## Noise suppression in front of the recognizer

| | WER 15 dB | WER 10 dB | busy |
|---|---|---|---|
| Kroko alone | 0.16 | 0.28 | 0.057 |
| GTCRN → Kroko | 0.21 | 0.35 | 0.160 |
| DPDFNet2 → Kroko | 0.20 | 0.30 | 0.242 |
| GTCRN → Moonshine Tiny | 0.50 (vs 0.44) | 0.62 (vs 0.54) | 0.230 |

Worse and more expensive: the recognizers cope better with the noise than with
the denoisers' artifacts. And the reSpeaker already suppresses noise in hardware. Dropped.

## Key word extraction variants (on each recognizer's output)

| Recognizer | Variant | recall | precision | top-5 ok |
|---|---|---|---|---|
| Moonshine Tiny | keywords.py as is | 0.68 | 0.68 | 0.62 |
| Moonshine Tiny | + drop unknown words | 0.68 | 0.69 | 0.65 |
| Kroko | keywords.py as is | 0.86 | 0.83 | 0.75 |
| Kroko | + drop unknown words | 0.86 | 0.86 | 0.81 |
| Kroko, 0.5 s pauses | keywords.py as is | 0.87 | 0.86 | 0.78 |
| **Kroko, 0.5 s pauses** | **+ drop unknown words** | **0.87** | **0.89** | **0.84** |
| NeMo RNN-T | + drop unknown words | 0.67 | 0.82 | 0.78 |

"Drop unknown words": single words with wordfreq zipf < 1.0 (never seen in English,
nearly always misheard, e.g. "pung", "bellad") are not key words. Never cost a real
one. "Nouns and names only" added nothing on top. Now in `python/keywords.py`.

## Decision and next step on the board

Implemented (not yet active): `python/speech_kroko.py` — same interface as
`speech.py`, switch in `python/main.py` (`ASR = "kroko"`), model downloads itself on
first start (57 MB, CC-BY-SA, Banafo Kroko ASR). Tested on the laptop the way
main.py calls it.

Two configurations to confirm on the board (live eval + `bench_asr.py` on the same
24 files, against Moonshine Tiny):

1. **Kroko, 2 threads, lines end at 0.5 s pauses, unknown-word filter** (expected winner)
2. Same with **4 threads** — the open question is speed on the ARM cores.

Caveat: synthetic voices. Kroko may like Piper's voices more than real people;
the live test with real speech at the table decides.
