#!/usr/bin/env python3
"""Play scripted dinner conversations into the running app and score what it makes of them.

    export BOARD=arduino@<board address>
    python3 tools/eval/eval.py                        # all scenarios
    python3 tools/eval/eval.py 02_new_job --snr 5     # one scenario, louder restaurant
    python3 tools/eval/eval.py --seed 7 --tag pause   # same audio as another run with seed 7

For each scenario (tools/eval/scenarios/*.txt):

1. Synthesize it like a real table, not a studio: one voice per speaker, each with
   its own pitch, pace and distance to the mic; room reverb, natural pauses,
   overlapping turns and backchannels ("yeah", "mhm"); restaurant background noise
   at the given signal-to-noise ratio (default 10 dB: a busy restaurant). Voices
   come from Piper (tools/eval/.cache/piper/) if present, else espeak-ng. --seed
   fixes all random choices, so that runs can be compared.
2. Upload the WAV to .board/test/ on the board. The app plays it instead of the
   microphone (python/microphone.py), which bypasses the reSpeaker's own noise
   suppression: a worst case.
3. Collect the recognized lines and key words from the app log, and what the
   screen shows at the end.
4. Score against the script:
   - WER: word error rate of the transcript (0 = perfect)
   - key word recall: share of the expected key words (# expect:) that were found
   - key word precision: share of the found key words that were really said
   - screen recall: share of the expected key words on the screen at the end
   - screen ok: share of the words on the screen that are expected or fine (# ok:)

Results go to tools/eval/out/. Needs python3 (standard library only), ffmpeg,
espeak-ng or Piper, and ssh access to the board. Background noise: one channel of
the PRESTO recording of the DEMAND corpus (CC BY 4.0, https://zenodo.org/records/1227121)
saved as tools/eval/.cache/restaurant_noise.wav; without it, white noise is used.
"""

import argparse
import json
import math
import os
import random
import re
import selectors
import subprocess
import sys
import tempfile
import time
import wave
from array import array
from html import unescape
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios"
OUT = HERE / "out"
CACHE = HERE / ".cache"
NOISE_FILE = CACHE / "restaurant_noise.wav"
PIPER = CACHE / "piper" / "piper"
PIPER_MODEL = CACHE / "en_US-libritts_r-medium.onnx"
APP_DIR = "/home/arduino/ArduinoApps/keep_up"
CONTAINER = "keep_up-main-1"
RATE = 16000


class Voice(NamedTuple):
    espeak: str  # espeak-ng voice ...
    pitch: int  # ... its pitch, 0..99 ...
    wpm: int  # ... and words per minute
    piper: int  # Piper speaker id
    gain_db: float  # distance to the mic ...
    lowpass: int | None  # ... and the high frequencies lost on the way, Hz


SPEAKERS = {
    "A": Voice("en-us+m3", pitch=40, wpm=160, piper=12, gain_db=0, lowpass=None),
    "B": Voice("en-gb+f3", pitch=65, wpm=170, piper=90, gain_db=-4, lowpass=6500),
    "C": Voice("en-us+f2", pitch=55, wpm=150, piper=237, gain_db=-9, lowpass=4500),
}
PACE_JITTER = 0.12  # each sentence up to this much faster or slower
REVERB = "aecho=0.8:0.7:23|47|71:0.32|0.21|0.12"  # small, busy room
LEAD_IN = 1.0  # seconds of background before the first line
GAP = (0.15, 0.9)  # pause between turns, seconds
OVERLAP_CHANCE, OVERLAP = 0.2, (0.2, 0.6)  # the next speaker starts this early
BACKCHANNEL_CHANCE = 0.35  # someone says "yeah" over a sentence
BACKCHANNELS = ["yeah", "mhm", "right", "oh wow", "really?", "uh huh", "oh no"]
BACKCHANNEL_DB = -6
AFTER_END = 1.0  # keep reading the log this long after "test: end"
APP_TAIL = 8.0  # silence the app plays after the WAV (python/microphone.py)
TIMEOUT_EXTRA = 80.0  # give up this long after the audio should have ended

# What the app logs (python/speech.py, python/display.py)
TRANSCRIPT_LINE = re.compile(r'^speech: heard "(.*)"  \[')
LATENCY = re.compile(r"last update (\d+) ms\]")
DROPPED = re.compile(r"dropped ([\d.]+) s of audio")
INTERESTING = ("test:", "speech: heard", "speech: busy", "keywords:")


# --- scenario and audio --------------------------------------------------------------


def load_scenario(path: Path) -> dict:
    """Lines "A: ...", plus "# expect:" key words that must be found ("a|b": either
    counts) and "# ok:" words that are fine to show too."""
    expect, ok, lines = [], set(), []
    for raw in path.read_text().splitlines():
        if raw.startswith("# expect:"):
            expect += [
                {alt.strip().lower() for alt in group.split("|")}
                for group in raw.split(":", 1)[1].split(",")
                if group.strip()
            ]
        elif raw.startswith("# ok:"):
            ok |= {w.strip().lower() for w in raw.split(":", 1)[1].split(",") if w.strip()}
        elif m := re.match(r"^([A-Z]):\s*(.+)$", raw):
            lines.append((m[1], m[2].strip()))
    return {"name": path.stem, "expect": expect, "ok": ok, "lines": lines}


def synthesize(scenario: dict, snr_db: float | None, seed: int, out: Path) -> float:
    """Write the scenario as a realistic mono 16 kHz 16-bit WAV; returns its duration (s)."""
    rng = random.Random(seed)
    clips = []  # (start sample, samples)
    cursor = int(LEAD_IN * RATE)
    with tempfile.TemporaryDirectory() as tmp:
        for i, (who, text) in enumerate(scenario["lines"]):
            speech = _say(text, who, rng, Path(tmp), f"{i}")
            if i and rng.random() < OVERLAP_CHANCE:
                cursor -= int(rng.uniform(*OVERLAP) * RATE)  # interrupts the last speaker
            clips.append((cursor, speech))
            if rng.random() < BACKCHANNEL_CHANCE:
                other = rng.choice([s for s in SPEAKERS if s != who])
                word = rng.choice(BACKCHANNELS)
                backchannel = _say(word, other, rng, Path(tmp), f"{i}bc", BACKCHANNEL_DB)
                clips.append((cursor + int(len(speech) * rng.uniform(0.4, 0.8)), backchannel))
            cursor += len(speech) + int(rng.uniform(*GAP) * RATE)
    mix = [0.0] * (max(start + len(clip) for start, clip in clips) + RATE)
    for start, clip in clips:
        for j, v in enumerate(clip):
            mix[start + j] += v
    if snr_db is not None:
        _add_noise(mix, snr_db, rng)
    peak = max(abs(v) for v in mix) or 1.0
    scale = min(1.0, 29000 / peak)  # no clipping
    pcm = array("h", (int(v * scale) for v in mix))
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return len(pcm) / RATE


def _say(
    text: str, who: str, rng: random.Random, tmp: Path, tag: str, extra_db: float = 0
) -> list[int]:
    """One utterance: speech synthesis, then distance (gain, low-pass) and room reverb."""
    voice = SPEAKERS.get(who, SPEAKERS["A"])
    pace = 1 + rng.uniform(-PACE_JITTER, PACE_JITTER)
    raw, done = tmp / f"{tag}.wav", tmp / f"{tag}_16k.wav"
    if PIPER.exists() and PIPER_MODEL.exists():
        command = [str(PIPER), "-m", str(PIPER_MODEL), "-s", str(voice.piper)]
        command += ["--length_scale", f"{1 / pace:.2f}", "-f", str(raw)]
        subprocess.run(command, input=text, text=True, check=True, capture_output=True)
    else:
        command = ["espeak-ng", "-v", voice.espeak, "-p", str(voice.pitch)]
        command += ["-s", str(round(voice.wpm * pace)), "-w", str(raw), text]
        subprocess.run(command, check=True)
    filters = [f"lowpass=f={voice.lowpass}"] if voice.lowpass else []
    filters += [REVERB, f"volume={voice.gain_db + extra_db}dB"]
    command = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-af", ",".join(filters)]
    command += ["-ar", str(RATE), "-ac", "1", "-sample_fmt", "s16", str(done)]
    subprocess.run(command, check=True)
    with wave.open(str(done)) as w:
        return list(array("h", w.readframes(w.getnframes())))


def _add_noise(mix: list[float], snr_db: float, rng: random.Random) -> None:
    """Restaurant background (or white noise) at snr_db below the speech."""
    loud = [v for v in mix if abs(v) > 300]  # speech parts only
    speech_rms = math.sqrt(sum(v * v for v in loud) / max(len(loud), 1))
    if NOISE_FILE.exists():
        with wave.open(str(NOISE_FILE)) as w:
            bed = array("h", w.readframes(w.getnframes()))
        start = rng.randrange(max(1, len(bed) - len(mix)))
        noise = [bed[(start + i) % len(bed)] for i in range(len(mix))]
    else:
        noise = [rng.gauss(0, 1000) for _ in mix]
    noise_rms = math.sqrt(sum(v * v for v in noise) / len(noise)) or 1.0
    gain = speech_rms / 10 ** (snr_db / 20) / noise_rms
    for i, v in enumerate(noise):
        mix[i] += v * gain


# --- run on the board ------------------------------------------------------------------


def play_on_board(wav: Path, name: str, duration: float, board: str) -> tuple[list, str]:
    """Upload the WAV for the app to play; return the timed app log lines and the screen."""
    test_dir = f"{APP_DIR}/.board/test"
    remote = (
        f"set -e; ts=$(date +%s); mkdir -p {test_dir}; "
        f"cat > {test_dir}/.{name}.part; mv {test_dir}/.{name}.part {test_dir}/{name}.wav; "
        f"exec docker logs -t -f --since $ts {CONTAINER} 2>&1"
    )
    proc = subprocess.Popen(["ssh", board, remote], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    proc.stdin.write(wav.read_bytes())
    proc.stdin.close()

    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    lines, buffer, screen = [], b"", ""
    deadline = time.monotonic() + duration + TIMEOUT_EXTRA
    end_seen_at = None
    while time.monotonic() < deadline:
        if end_seen_at and time.monotonic() - end_seen_at > AFTER_END:
            break
        if not selector.select(timeout=0.5):
            continue
        chunk = proc.stdout.read1(65536)
        if not chunk:
            break
        buffer += chunk
        *complete, buffer = buffer.split(b"\n")
        for raw in complete:
            stamp, _, line = raw.decode(errors="replace").partition(" ")
            lines.append((_seconds(stamp), line))
            if line.startswith(f"test: audio {name}"):
                # should the end marker be missed, stop when the test must be over
                deadline = min(deadline, time.monotonic() + duration + APP_TAIL + 5)
            if line.startswith(f"test: end {name}") and end_seen_at is None:
                end_seen_at = time.monotonic()
                screen = screen_text(board)  # now, before the real microphone talks again
            if line.startswith(INTERESTING):
                print(f"   | {line}", flush=True)
    proc.kill()
    if end_seen_at is None:
        screen = screen or screen_text(board)
        print("   !! no 'test: end' in the log - is the deployed app up to date and running?")
    return lines, screen


def _seconds(docker_stamp: str) -> float:
    """'2026-11-13T17:46:45.123456789Z' -> seconds of the day (enough to order one run)."""
    m = re.search(r"T(\d+):(\d+):(\d+(?:\.\d+)?)", docker_stamp)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def screen_text(board: str) -> str:
    """What the screen shows: "[<knob level>] <words>"."""
    command = ["ssh", board, f"cat {APP_DIR}/.board/screen.json"]
    out = subprocess.run(command, capture_output=True, text=True).stdout
    try:
        frame = json.loads(out)
    except ValueError:
        return ""
    text = unescape(re.sub(r"<[^>]+>", "", frame.get("markup", ""))).strip()
    return f"[{frame.get('status', '?')}] {text}"


# --- scoring ---------------------------------------------------------------------------


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def stem(word: str) -> str:
    """Same thing, said differently: "Maria's" = "maria", "vets" = "vet"."""
    word = word.lower().removesuffix("'s").removesuffix("’s")
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def wer(ref: list[str], hyp: list[str]) -> float:
    """Word error rate: word-level edit distance over the reference length."""
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[len(hyp)] / max(len(ref), 1)


def score(scenario: dict, log: list[tuple[float, str]], screen: str, duration: float) -> dict:
    # Lines before "test: audio" were said in the real room (the app plays silence
    # first), lines well after "test: end" too (the microphone is back).
    first = log[0][0] if log else 0.0
    begin = next((t for t, line in log if line.startswith("test: audio")), first)
    end = next((t for t, line in log if line.startswith("test: end")), begin + duration + APP_TAIL)
    run = [line for t, line in log if begin <= t <= end + AFTER_END]
    said = [m[1] for line in run if (m := TRANSCRIPT_LINE.match(line)) and m[1]]
    found = [
        keyword.strip()
        for line in run
        if line.startswith("keywords:")
        for keyword in line.split(":", 1)[1].split(",")
        if keyword.strip() not in ("", "-")
    ]
    dropped = sum(float(m[1]) for line in run if (m := DROPPED.search(line)))
    latencies = [int(m[1]) for line in run if (m := LATENCY.search(line))]

    ref_words = words(" ".join(text for _, text in scenario["lines"]))
    ref_stems = {stem(w) for w in ref_words}
    found_lc = {k.lower() for k in found}
    found_stems = {" ".join(stem(w) for w in words(k)) for k in found_lc}
    screen_stems = {stem(w) for w in words(screen)}

    def hit(group: set[str], pool: set[str]) -> bool:
        return any(" ".join(stem(w) for w in words(alt)) in pool for alt in group)

    expect = scenario["expect"]
    recalled = [sorted(g)[0] for g in expect if hit(g, found_stems)]
    on_screen = [sorted(g)[0] for g in expect if hit(g, screen_stems)]
    level, _, screen = screen.partition("] ")
    shown = words(screen)
    fine_stems = {" ".join(stem(w) for w in words(alt)) for g in expect for alt in g}
    fine_stems |= {stem(w) for w in scenario["ok"]}
    shown_ok = [w for w in shown if stem(w) in fine_stems]
    really_said = [k for k in found_lc if all(stem(w) in ref_stems for w in words(k))]
    return {
        "scenario": scenario["name"],
        "wer": round(wer(ref_words, words(" ".join(said))), 3),
        "keyword_recall": round(len(recalled) / max(len(expect), 1), 3),
        "keyword_precision": round(len(really_said) / max(len(found_lc), 1), 3),
        "screen_recall": round(len(on_screen) / max(len(expect), 1), 3),
        "screen_ok": round(len(shown_ok) / max(len(shown), 1), 3),
        "missed_keywords": [sorted(g)[0] for g in expect if not hit(g, found_stems)],
        "invented_keywords": sorted(found_lc - set(really_said)),
        "dropped_audio_s": round(dropped, 1),
        "avg_update_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "transcript": said,
        "keywords": found,
        "screen": screen,
        "screen_level": level.lstrip("["),
        "reference": [text for _, text in scenario["lines"]],
    }


# --- main ------------------------------------------------------------------------------


def _row(name: str, r: dict) -> str:
    return (
        f"{name:22s} {r['wer']:5.2f} {r['keyword_recall']:9.2f} {r['keyword_precision']:8.2f} "
        f"{r['screen_ok']:9.2f} {r['dropped_audio_s']:7.1f}s"
    )


def print_summary(results: list[dict]) -> dict:
    """Print a table of the results; returns their averages."""
    print(
        f"\n{'scenario':22s} {'WER':>5s} {'kw recall':>9s} {'kw prec':>8s} "
        f"{'screen ok':>9s} {'dropped':>8s} {'upd ms':>7s}"
    )
    for r in results:
        print(f"{_row(r['scenario'], r)} {r['avg_update_ms'] or '-':>7}")
        missed = ", ".join(r["missed_keywords"]) or "-"
        invented = ", ".join(r["invented_keywords"]) or "-"
        print(f"   missed: {missed}   invented: {invented}")
        print(f"   screen [{r['screen_level']}]: {r['screen']!r}")
    keys = ("wer", "keyword_recall", "keyword_precision", "screen_ok", "dropped_audio_s")
    average = {k: round(sum(r[k] for r in results) / len(results), 3) for k in keys}
    print(_row("AVERAGE", average))
    return average


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("scenarios", nargs="*", help="names in tools/eval/scenarios (default: all)")
    parser.add_argument(
        "--snr", type=float, default=10.0,
        help="restaurant noise this many dB below the speech (default 10; 20 quiet, 5 loud)",
    )
    parser.add_argument("--clean", action="store_true", help="no background noise")
    parser.add_argument("--seed", type=int, help="fix voices, timing and noise to compare runs")
    parser.add_argument(
        "--board", default=os.environ.get("BOARD"),
        help="ssh target of the board, e.g. arduino@192.168.1.20 (default: $BOARD)",
    )
    parser.add_argument("--tag", default="", help="label for this run in the results file")
    args = parser.parse_args()
    if not args.board:
        parser.error("set BOARD=arduino@<board address> or pass --board")

    names = [n.removesuffix(".txt") for n in args.scenarios]
    paths = [SCENARIOS / f"{n}.txt" for n in names] or sorted(SCENARIOS.glob("*.txt"))
    OUT.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    seed = args.seed if args.seed is not None else random.randrange(10_000)
    snr = None if args.clean else args.snr
    voices = "piper" if PIPER.exists() else "espeak-ng"
    results = []
    for path in paths:
        scenario = load_scenario(path)
        name = f"{stamp}_{scenario['name']}"
        wav = OUT / f"{name}.wav"
        duration = synthesize(scenario, snr, seed, wav)
        noise = "clean" if snr is None else f"restaurant noise, SNR {snr:g} dB"
        print(f"\n> {scenario['name']}  ({duration:.0f} s audio, {noise}, seed {seed}, {voices})")
        log, screen = play_on_board(wav, name, duration, args.board)
        results.append(score(scenario, log, screen, duration))

    average = print_summary(results)
    report = OUT / f"{stamp}{'_' + args.tag if args.tag else ''}.json"
    summary = {"tag": args.tag, "snr": snr, "seed": seed, "average": average, "results": results}
    report.write_text(json.dumps(summary, indent=2))
    print(f"\nreport: {report.relative_to(HERE.parents[1])}")


if __name__ == "__main__":
    sys.exit(main())
