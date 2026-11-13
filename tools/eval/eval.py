#!/usr/bin/env python3
"""Play scripted dinner conversations into the running app and score what it makes of them.

    python3 tools/eval/eval.py                      # all scenarios
    python3 tools/eval/eval.py 02_new_job --snr 5   # one scenario, louder restaurant
    python3 tools/eval/eval.py --seed 7 --tag vad05 # same audio as another run with seed 7

For each scenario (tools/eval/scenarios/*.txt):
1. Synthesize it like a real table, not a studio:
   - one voice per speaker (A, B, C), each with its own pitch, pace and distance
     to the mic (quieter and duller further away), pace varying per sentence
   - room reverb, natural pauses, sometimes the next person starts before the
     last one finished, and short backchannels ("yeah", "mhm") talk over people
   - real restaurant background noise (DEMAND corpus, PRESTO, CC BY 4.0) at the
     given signal-to-noise ratio (default 10 dB: a busy restaurant)
   Voices: Piper (natural, tools/eval/.cache/piper/) if present, else espeak-ng.
   --seed fixes all random choices: same seed = same audio, to compare changes.
2. Drop the WAV into .board/test/ on the board; the app plays it instead of the
   microphone (python/microphone.py) and marks it "[TEST] start/end" in its log.
   (So the reSpeaker's own noise suppression is bypassed: a worst case.)
3. Collect the recognized lines and key words from the log, and what the screen
   shows at the end (.board/screen.json).
4. Score against the script:
   - WER: word error rate of the transcript (0 = perfect)
   - key word recall: share of the expected key words (# expect:) that were found
   - key word precision: share of the found key words that were really said
   - on screen: expected key words visible at the end
   Results go to tools/eval/out/ (gitignored).

Laptop needs: python3 (standard library only), espeak-ng, ffmpeg, ssh access to the
board; tools/eval/.cache/restaurant_noise.wav (one channel of DEMAND PRESTO_16k.zip,
https://zenodo.org/records/1227121) - without it, white noise is used.
"""

import argparse
import json
from array import array
import math
import random
import re
import selectors
import subprocess
import sys
import tempfile
import time
import wave
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios"
OUT = HERE / "out"
BOARD = "arduino@192.168.1.216"
APP_DIR = "/home/arduino/ArduinoApps/keep_up"
CONTAINER = "keep_up-main-1"

RATE = 16000
CACHE = HERE / ".cache"
NOISE_FILE = CACHE / "restaurant_noise.wav"
PIPER = CACHE / "piper" / "piper"
PIPER_MODEL = CACHE / "en_US-libritts_r-medium.onnx"

# Who sits where: voice, pitch / pace, and distance to the mic (gain, high frequencies lost)
SPEAKERS = {
    "A": {"espeak": "en-us+m3", "pitch": 40, "wpm": 160, "piper": 12, "gain_db": 0, "lowpass": None},
    "B": {"espeak": "en-gb+f3", "pitch": 65, "wpm": 170, "piper": 90, "gain_db": -4, "lowpass": 6500},
    "C": {"espeak": "en-us+f2", "pitch": 55, "wpm": 150, "piper": 237, "gain_db": -9, "lowpass": 4500},
}
PACE_JITTER = 0.12  # each sentence up to this much faster/slower
REVERB = "aecho=0.8:0.7:23|47|71:0.32|0.21|0.12"  # small, busy room
LEAD_IN = 1.0  # seconds of background before the first line
GAP = (0.15, 0.9)  # pause between turns, seconds
OVERLAP_CHANCE, OVERLAP = 0.2, (0.2, 0.6)  # next speaker starts this early
BACKCHANNEL_CHANCE = 0.35  # someone says "yeah" over a sentence
BACKCHANNELS = ["yeah", "mhm", "right", "oh wow", "really?", "uh huh", "oh no"]
BACKCHANNEL_DB = -6
AFTER_END = 1.0  # keep reading the log this long after "[TEST] end"
APP_LEAD, APP_TAIL = 10.0, 8.0  # silence the app plays before / after the WAV (microphone.py)
TIMEOUT_EXTRA = 80.0  # give up this long after the audio should have ended


# --- scenario & audio -----------------------------------------------------------------

def load_scenario(path: Path) -> dict:
    """# expect: must-have key words; # ok: fine to show too (not required)."""
    expect, ok, lines = [], set(), []
    for raw in path.read_text().splitlines():
        if raw.startswith("# expect:"):
            expect += [{alt.strip().lower() for alt in group.split("|")}
                       for group in raw.split(":", 1)[1].split(",") if group.strip()]
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
                bc = _say(rng.choice(BACKCHANNELS), other, rng, Path(tmp), f"{i}bc", BACKCHANNEL_DB)
                clips.append((cursor + int(len(speech) * rng.uniform(0.4, 0.8)), bc))
            cursor += len(speech) + int(rng.uniform(*GAP) * RATE)
    mix = [0.0] * (max(start + len(c) for start, c in clips) + RATE)
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


def _say(text: str, who: str, rng: random.Random, tmp: Path, tag: str, extra_db: float = 0) -> list[float]:
    """One utterance: TTS, then distance (gain, low-pass) and room reverb."""
    voice = SPEAKERS.get(who, SPEAKERS["A"])
    pace = 1 + rng.uniform(-PACE_JITTER, PACE_JITTER)
    raw, done = tmp / f"{tag}.wav", tmp / f"{tag}_16k.wav"
    if PIPER.exists() and PIPER_MODEL.exists():
        subprocess.run([str(PIPER), "-m", str(PIPER_MODEL), "-s", str(voice["piper"]),
                        "--length_scale", f"{1 / pace:.2f}", "-f", str(raw)],
                       input=text, text=True, check=True, capture_output=True)
    else:
        subprocess.run(["espeak-ng", "-v", voice["espeak"], "-p", str(voice["pitch"]),
                        "-s", str(round(voice["wpm"] * pace)), "-w", str(raw), text], check=True)
    filters = [f"lowpass=f={voice['lowpass']}"] if voice["lowpass"] else []
    filters += [REVERB, f"volume={voice['gain_db'] + extra_db}dB"]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-af", ",".join(filters),
                    "-ar", str(RATE), "-ac", "1", "-sample_fmt", "s16", str(done)], check=True)
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


# --- run on the board -----------------------------------------------------------------

def play_on_board(wav: Path, name: str, duration: float, board: str) -> tuple[list, str]:
    """Upload the WAV for the app to play; return the timed app log lines and the screen."""
    remote = (f"set -e; ts=$(date +%s); mkdir -p {APP_DIR}/.board/test; "
              f"cat > {APP_DIR}/.board/test/.{name}.part; "
              f"mv {APP_DIR}/.board/test/.{name}.part {APP_DIR}/.board/test/{name}.wav; "
              f"exec docker logs -t -f --since $ts {CONTAINER} 2>&1")
    proc = subprocess.Popen(["ssh", board, remote], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=False)
    proc.stdin.write(wav.read_bytes())
    proc.stdin.close()

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    lines, buf, screen = [], b"", ""
    deadline = time.monotonic() + duration + TIMEOUT_EXTRA
    audio_seen_at = None
    end_seen_at = None
    while time.monotonic() < deadline:
        if end_seen_at and time.monotonic() - end_seen_at > AFTER_END:
            break
        if not sel.select(timeout=0.5):
            continue
        chunk = proc.stdout.read1(65536)
        if not chunk:
            break
        buf += chunk
        *done, buf = buf.split(b"\n")
        for raw in done:
            stamp, _, line = raw.decode(errors="replace").partition(" ")
            lines.append((_seconds(stamp), line))
            if line.startswith(f"[TEST] audio {name}"):
                audio_seen_at = time.monotonic()
                # should the end marker be missed, stop when the test must be over
                deadline = min(deadline, audio_seen_at + duration + APP_TAIL + 5)
            if line.startswith(f"[TEST] end {name}") and end_seen_at is None:
                end_seen_at = time.monotonic()
                screen = screen_text(board)  # now: before the real microphone talks again
            if _interesting(line):
                print(f"   | {line}", flush=True)
    proc.kill()
    if end_seen_at is None and not screen:
        screen = screen_text(board)
    if end_seen_at is None:
        print("   !! no '[TEST] end' in the log - is the deployed app up to date and running?")
    return lines, screen


def _seconds(docker_stamp: str) -> float:
    """'2026-11-13T17:46:45.123456789Z' -> seconds of the day (enough to order one run)."""
    m = re.search(r"T(\d+):(\d+):(\d+(?:\.\d+)?)", docker_stamp)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def _interesting(line: str) -> bool:
    return line.startswith(("[TEST]", "📝", "   key words", "speech: busy"))


def screen_text(board: str) -> str:
    out = subprocess.run(["ssh", board, f"cat {APP_DIR}/.board/screen.json"],
                         capture_output=True, text=True).stdout
    try:
        frame = json.loads(out)
    except ValueError:
        return ""
    text = unescape(re.sub(r"<[^>]+>", "", frame.get("markup", ""))).replace("●", "").strip()
    return f"[{frame.get('status', '?')}] {text}"  # status = knob level (number of words or LIVE)


# --- scoring ----------------------------------------------------------------------------

def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def stem(word: str) -> str:
    """Same thing, said differently: "Maria's" = "maria", "vets" = "vet"."""
    word = word.lower().removesuffix("'s").removesuffix("’s")
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[len(hyp)] / max(len(ref), 1)


def score(scenario: dict, log: list[tuple[float, str]], screen: str, duration: float) -> dict:
    # Lines before "[TEST] audio" were said in the real room (the app plays silence first),
    # lines well after "[TEST] end" too (the microphone is back).
    begin = next((t for t, l in log if l.startswith("[TEST] audio")), log[0][0] if log else 0.0)
    end = next((t for t, l in log if l.startswith("[TEST] end")), begin + duration + APP_TAIL)
    run = [l for t, l in log if begin <= t <= end + AFTER_END]
    said = [re.sub(r"\s*\[[\d.]+s of speech.*$", "", l[1:].strip()) for l in run if l.startswith("📝")]
    said = [t for t in said if t]
    found = [k.strip() for l in run if l.startswith("   key words:")
             for k in l.split(":", 1)[1].split(",") if k.strip() and k.strip() != "-"]
    dropped = sum(float(m[1]) for l in run if (m := re.search(r"dropped ([\d.]+)s", l)))
    latencies = [int(m[1]) for l in run if (m := re.search(r"took (\d+) ms", l))]

    ref_text = " ".join(t for _, t in scenario["lines"])
    ref_words = words(ref_text)
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
    shown = [w for w in words(screen) if w]
    expected_stems = {" ".join(stem(w) for w in words(alt)) for g in expect for alt in g}
    expected_stems |= {stem(w) for w in scenario["ok"]}
    shown_ok = [w for w in shown if stem(w) in expected_stems]
    really_said = [k for k in found_lc if all(stem(w) in ref_stems for w in words(k))]
    return {
        "scenario": scenario["name"],
        "wer": round(wer(ref_words, words(" ".join(said))), 3),
        "keyword_recall": round(len(recalled) / max(len(expect), 1), 3),
        "keyword_precision": round(len(really_said) / max(len(found_lc), 1), 3),
        "screen_recall": round(len(on_screen) / max(len(expect), 1), 3),
        "screen_ok": round(len(shown_ok) / max(len(shown), 1), 3),  # shown words that matter
        "missed_keywords": [sorted(g)[0] for g in expect if not hit(g, found_stems)],
        "invented_keywords": sorted(found_lc - set(really_said)),
        "dropped_audio_s": round(dropped, 1),
        "avg_update_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "transcript": said,
        "keywords": found,
        "screen": screen,
        "screen_level": level.lstrip("["),
        "reference": [t for _, t in scenario["lines"]],
    }


# --- main --------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="*", help="names in tools/eval/scenarios (default: all)")
    ap.add_argument("--snr", type=float, default=10.0,
                    help="restaurant noise this many dB below the speech (default 10; 20 quiet, 5 loud)")
    ap.add_argument("--clean", action="store_true", help="no background noise")
    ap.add_argument("--seed", type=int, help="fix voices/timing/noise to compare runs (default: new each run)")
    ap.add_argument("--board", default=BOARD)
    ap.add_argument("--tag", default="", help="label for this run in the results file")
    args = ap.parse_args()

    paths = [SCENARIOS / f"{n.removesuffix('.txt')}.txt" for n in args.scenarios] or sorted(SCENARIOS.glob("*.txt"))
    OUT.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    seed = args.seed if args.seed is not None else random.randrange(10_000)
    snr = None if args.clean else args.snr
    results = []
    for path in paths:
        scenario = load_scenario(path)
        name = f"{stamp}_{scenario['name']}"
        wav = OUT / f"{name}.wav"
        duration = synthesize(scenario, snr, seed, wav)
        noise = "clean" if snr is None else f"restaurant noise, SNR {snr:g} dB"
        print(f"\n▶ {scenario['name']}  ({duration:.0f}s audio, {noise}, seed {seed}, "
              f"{'piper' if PIPER.exists() else 'espeak-ng'} voices)")
        log, screen = play_on_board(wav, name, duration, args.board)
        results.append(score(scenario, log, screen, duration))

    print(f"\n{'scenario':22s} {'WER':>5s} {'kw recall':>9s} {'kw prec':>8s} {'screen ok':>9s} {'dropped':>8s} {'upd ms':>7s}")
    for r in results:
        print(f"{r['scenario']:22s} {r['wer']:5.2f} {r['keyword_recall']:9.2f} {r['keyword_precision']:8.2f} "
              f"{r['screen_ok']:9.2f} {r['dropped_audio_s']:7.1f}s {r['avg_update_ms'] or '-':>7}")
        print(f"   missed: {', '.join(r['missed_keywords']) or '-'}   invented: {', '.join(r['invented_keywords']) or '-'}")
        print(f"   screen [{r['screen_level']}]: {r['screen']!r}")
    keys = ("wer", "keyword_recall", "keyword_precision", "screen_ok", "dropped_audio_s")
    avg = {k: round(sum(r[k] for r in results) / len(results), 3) for k in keys}
    print(f"{'AVERAGE':22s} {avg['wer']:5.2f} {avg['keyword_recall']:9.2f} {avg['keyword_precision']:8.2f} "
          f"{avg['screen_ok']:9.2f} {avg['dropped_audio_s']:7.1f}s")
    report = OUT / f"{stamp}{'_' + args.tag if args.tag else ''}.json"
    report.write_text(json.dumps({"tag": args.tag, "snr": snr, "seed": seed, "average": avg, "results": results}, indent=2))
    print(f"\nreport: {report.relative_to(HERE.parents[1])}")


if __name__ == "__main__":
    sys.exit(main())
