# Choosing the speech recognizer

keep_up needs streaming speech recognition that runs offline on the four
Cortex-A53 cores of the UNO Q, keeps up with a lively table, and gets names and
nouns right, because those are what ends up on the screen. This note records how
the recognizer was chosen (November 2026).

## Method

- **Test set**: the six scripted dinner conversations in `tools/eval/scenarios/`,
  each in four conditions: clean, restaurant noise at 15 dB (two different mixes)
  and at 10 dB. 24 files, 455 s in total, synthesized by `tools/eval/eval.py`:
  Piper voices for three speakers at different distances, room reverb,
  overlapping turns, backchannels and real restaurant noise (DEMAND, PRESTO).
  The reSpeaker's own noise suppression is bypassed, so this is a worst case.
- **Run**: every file was streamed in 0.1 s chunks into each recognizer, pinned
  to four cores like the board. Key words were extracted from the recognized
  lines with the app's own `python/keywords.py`.
- **Hardware**: a laptop, as the board was not available. Accuracy carries over
  (same model files, same int8 arithmetic); speed only relatively.
- **busy**: wall time per second of audio, as in the app's `speech: busy` log.
  Laptop values vary by ±50 %; read them as an order of magnitude.
- **top-5 ok**: of the five strongest key words of a conversation (roughly what
  the screen shows), the share that is expected or fine for that conversation.

## Recognizers

| Model | WER clean | WER 15 dB | WER 10 dB | busy | Key word recall | Key word precision |
|---|---|---|---|---|---|---|
| Moonshine Tiny | 0.31 | 0.44 | 0.54 | 0.156 | 0.68 | 0.68 |
| Moonshine Small | 0.19 | 0.30 | 0.38 | 0.529 | 0.78 | 0.79 |
| Kroko, 4 threads | 0.06 | 0.16 | 0.28 | 0.057 | 0.86 | 0.83 |
| Kroko, 2 threads | 0.06 | 0.15 | 0.29 | **0.024** | 0.86 | 0.83 |
| **Kroko, lines end at 0.5 s pauses** | **0.04** | **0.14** | **0.27** | 0.096 | **0.87** | **0.86** |
| Kroko, lines end at 0.3 s pauses | 0.04 | 0.14 | 0.27 | 0.084 | 0.87 | 0.86 |
| Kroko + punctuation model | 0.06 | 0.16 | 0.28 | 0.066 | 0.86 | 0.83 |
| NeMo FastConformer RNN-T 480 ms + punct. | 0.13 | 0.30 | 0.47 | 0.175 | 0.67 | 0.81 |
| NeMo FastConformer CTC 480 ms + punct. | 0.15 | 0.32 | 0.46 | 0.058 | 0.62 | 0.71 |
| NeMo FastConformer CTC 80 ms + punct. | 0.14 | 0.31 | 0.50 | 0.157 | 0.61 | 0.72 |
| Zipformer en 2023-06-26 + punct. | 0.30 | 0.33 | 0.46 | 0.143 | 0.54 | 0.64 |
| Zipformer en 20M + punct. | 0.32 | 0.39 | 0.55 | 0.069 | 0.48 | 0.54 |

On the board itself, Moonshine Tiny ran at busy ≈ 0.5–1.0 (keeping up with
little headroom) and Moonshine Small at ≈ 1.2–2.0 (falling behind and dropping
audio). The board is about four times slower than the laptop, which puts Kroko
at roughly 0.1–0.4.

Kroko beat Moonshine Tiny on every one of the 24 files, and Moonshine Small at a
fraction of its cost. It writes punctuation and capitals itself, so an extra
punctuation model adds nothing. Its default forced line cut after 8 s split
words ("Kyot | o"); ending lines at pauses only fixed that.

## Noise suppression in front of the recognizer

| | WER 15 dB | WER 10 dB | busy |
|---|---|---|---|
| Kroko alone | 0.16 | 0.28 | 0.057 |
| GTCRN → Kroko | 0.21 | 0.35 | 0.160 |
| DPDFNet2 → Kroko | 0.20 | 0.30 | 0.242 |
| GTCRN → Moonshine Tiny | 0.50 | 0.62 | 0.230 |

Worse and more expensive: the recognizers cope better with the noise than with
the denoisers' artifacts, and the reSpeaker already suppresses noise in hardware.

## Key word filter

| Recognizer | `keywords.py` | Recall | Precision | top-5 ok |
|---|---|---|---|---|
| Moonshine Tiny | without filter | 0.68 | 0.68 | 0.62 |
| Moonshine Tiny | dropping unknown words | 0.68 | 0.69 | 0.65 |
| Kroko | without filter | 0.86 | 0.83 | 0.75 |
| Kroko | dropping unknown words | 0.86 | 0.86 | 0.81 |
| Kroko, 0.5 s pauses | without filter | 0.87 | 0.86 | 0.78 |
| **Kroko, 0.5 s pauses** | **dropping unknown words** | **0.87** | **0.89** | **0.84** |

"Unknown words" are single words with a wordfreq Zipf frequency below 1.0. They
are nearly always misheard ("pung", "bellad"), and the filter never cost a real
key word. Keeping only nouns and names on top of it added nothing.

## Result

keep_up uses Kroko with two threads, lines that end at 0.5 s pauses, and the
unknown-word filter (`python/speech.py`, `python/keywords.py`). The test voices
are synthetic, and Kroko may suit them better than real speakers, so the final
check is always a real conversation at the table.
