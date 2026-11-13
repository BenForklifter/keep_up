"""Compare key word extraction variants on recognized lines from bench_asr.py.

    ... same docker/podman call as bench_asr.py, script bench_keywords.py [model ...]

Reads /bench/results/<model>.json (lines per test file), runs each variant of the
app's keywords.py over them and scores against the scenario's expectations:
recall (expected key words found), precision (found key words really said) and
top-5 ok (of the 5 strongest key words of the whole conversation - roughly what
the screen ends up showing - how many are expected or fine).
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import eval as ev
import keywords as kw

BENCH = Path("/bench")
KNOWN_ZIPF = 1.0  # below: (almost) never seen in English text


def variant(name: str):
    """keywords() with one change applied."""
    def extract(text):
        found = kw.keywords(text)
        if "known" in name:  # single words English has (almost) never seen are mostly misheard
            found = [k for k in found if " " in k.text or kw.zipf_frequency(k.text.lower(), "en") >= KNOWN_ZIPF]
        if "nouns" in name:  # nouns and names only
            doc = {t.text: t.pos_ for t in kw.NLP(text)}
            found = [k for k in found if " " in k.text or doc.get(k.text) in ("NOUN", "PROPN")]
        return found
    return extract


VARIANTS = ["base", "known", "nouns", "known+nouns"]


def score(rows, refs, extract):
    out = defaultdict(list)
    for row in rows:
        ref = refs[row["file"]]
        expect = [set(g) for g in ref["expect"]]
        good = {ev.stem(w) for g in expect for a in g for w in ev.words(a)} | {ev.stem(w) for o in ref["ok"] for w in ev.words(o)}
        ref_stems = {ev.stem(w) for w in ev.words(ref["reference"])}
        strength = defaultdict(float)
        found = set()
        for line in row["lines"]:
            for k in extract(line):
                found.add(k.text.lower())
                strength[k.text.lower()] += k.weight
        stems = {" ".join(ev.stem(w) for w in ev.words(k)) for k in found}
        hit = [g for g in expect if any(" ".join(ev.stem(w) for w in ev.words(a)) in stems for a in g)]
        said = [k for k in found if all(ev.stem(w) in ref_stems for w in ev.words(k))]
        top = sorted(strength, key=strength.get, reverse=True)[:5]
        top_ok = [k for k in top if all(ev.stem(w) in good for w in ev.words(k))]
        out["recall"].append(len(hit) / len(expect))
        out["precision"].append(len(said) / max(len(found), 1))
        out["top5_ok"].append(len(top_ok) / max(len(top), 1))
    return {k: round(sum(v) / len(v), 3) for k, v in out.items()}


def main(models):
    refs = json.loads((BENCH / "audio" / "refs.json").read_text())
    results = {}
    for model in models:
        rows = json.loads((BENCH / "results" / f"{model}.json").read_text())["rows"]
        for v in VARIANTS:
            r = score(rows, refs, variant(v))
            results[f"{model} | {v}"] = r
            print(f"{model:24s} {v:12s} recall {r['recall']:.2f}  precision {r['precision']:.2f}  top5 ok {r['top5_ok']:.2f}", flush=True)
    (BENCH / "results" / "keywords.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
