"""Pick the most important words of a piece of English speech.

A word matters when it is rare in everyday English: "telescope" tells you more
than "really". Rarity comes from wordfreq, word frequencies measured over
billions of words of text and subtitles (in effect an IDF score). On top of that:
names (capitalised mid-sentence) and words repeated in the text get a boost, and
among equal words the newer one wins. Cheap enough to run every second on the board.

    important_words("We pointed the new telescope at Jupiter.", 2)
    -> ["telescope", "Jupiter"]
"""

import re

from wordfreq import zipf_frequency

# Zipf scale: log10 of occurrences per billion words. "the" ~7.7, "telescope" ~3.6,
# unknown words 0. Rarity = how far below MAX_ZIPF a word is.
MAX_ZIPF = 7.0
MIN_RARITY = 1.5  # words more common than this (zipf > 5.5) are never "important"
NAME_BONUS = 1.5
REPEAT_BONUS = 0.3  # per extra occurrence, relative
RECENCY_BONUS = 0.5  # newest word gets this much, the oldest nothing

TOKEN = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)*")
SENTENCE_END = re.compile(r"[.!?]\s*$")

# Function words and spoken fillers. Rarity already ranks most of them low; this
# list catches the ones that are rare in writing but common in speech.
STOPWORDS = frozenset("""
    a about above after again against all also am an and any are aren't as at be
    because been before being below between both but by can can't cannot could
    couldn't did didn't do does doesn't doing don't down during each few for from
    further had hadn't has hasn't have haven't having he he'd he'll he's her here
    here's hers herself him himself his how how's i i'd i'll i'm i've if in into is
    isn't it it's its itself let's me more most mustn't my myself no nor not of off
    on once only or other ought our ours ourselves out over own same shan't she
    she'd she'll she's should shouldn't so some such than that that's the their
    theirs them themselves then there there's these they they'd they'll they're
    they've this those through to too under until up very was wasn't we we'd we'll
    we're we've were weren't what what's when when's where where's which while who
    who's whom why why's will with won't would wouldn't you you'd you'll you're
    you've your yours yourself yourselves
    actually anyway basically gonna gotta kinda like literally maybe mhm oh ok okay
    right sorta stuff thing things uh uhm um wanna well yeah yep yes
    get gets got going know knew mean means say said says see think thought want
""".split())


def important_words(text: str, count: int) -> list[str]:
    """The `count` most important distinct words of `text`, in spoken order."""
    candidates = {}  # lowercase key -> (score, position, word as spoken)
    tokens = list(TOKEN.finditer(text))
    for position, match in enumerate(tokens):
        word = match.group().replace("’", "'")
        key = _key(word)
        if key in STOPWORDS or len(key) < 2:
            continue
        rarity = MAX_ZIPF - zipf_frequency(key, "en")
        if rarity < MIN_RARITY:
            continue

        score = rarity
        if _is_name(word, text[: match.start()]):
            score += NAME_BONUS
        score += RECENCY_BONUS * position / max(1, len(tokens) - 1)

        if key in candidates:
            previous, _, _ = candidates[key]
            score = max(score, previous) + REPEAT_BONUS * rarity
        candidates[key] = (score, position, word)

    best = sorted(candidates.values(), reverse=True)[:count]
    return [word for _, _, word in sorted(best, key=lambda c: c[1])]


def _key(word: str) -> str:
    key = word.lower()
    return key[:-2] if key.endswith("'s") else key


def _is_name(word: str, before: str) -> bool:
    """Capitalised, but not just because it starts a sentence (or is "I")."""
    starts_sentence = not before.strip() or SENTENCE_END.search(before)
    return word[0].isupper() and not starts_sentence and word.lower() != "i"
