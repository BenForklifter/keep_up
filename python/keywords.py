"""Find the words that carry a conversation: the things, names, places and plans.

    weight = kind of word * (1 + RARITY_GAIN * rarity)

- Kind. spaCy (en_core_web_sm) tags parts of speech and finds named entities.
  Names and places count most, then nouns; verbs and adjectives only when they
  are unusual ("divorced"). Names are kept whole ("New York", "Aunt Mary").
  Vague words that fill a noun slot without saying anything ("lots", "kind",
  "stuff", "people", "time") never count.
- Rarity in everyday English (wordfreq, frequencies over billions of words):
  "telescope" counts a bit more than "food". Only a modifier, so everyday topic
  words ("food", "kids", "words") still count, and capped, because the rarest
  "words" are often misheard ones.

While a line is still being spoken its last word is left out: the recognizer
often revises it ("dis" -> "display"), and showing its guesses makes nonsense
flash up.

    [k.text for k in keywords("We flew to New York with my sister's dogs.")]
    -> ["New York", "sister", "dogs"]   (each with key and weight)
"""

from dataclasses import dataclass

import spacy
from wordfreq import zipf_frequency


@dataclass(frozen=True)
class Keyword:
    key: str  # the same thing across mentions: lowercase lemma ("dogs" -> "dog")
    text: str  # as it was said ("dogs", "New York")
    weight: float  # how much it tells about the conversation, ~1.2 ... 3.3


# The parser is not needed (no syntax trees), skipping it saves most of the CPU.
NLP = spacy.load("en_core_web_sm", exclude=["parser"])

KIND = {"PROPN": 1.2, "NOUN": 1.0, "VERB": 0.6, "ADJ": 0.6}
ENTITY_KIND = {
    "PERSON": 1.5, "GPE": 1.5, "LOC": 1.5, "FAC": 1.5, "ORG": 1.4, "PRODUCT": 1.4,
    "EVENT": 1.4, "WORK_OF_ART": 1.4, "NORP": 1.3, "LANGUAGE": 1.3, "LAW": 1.2,
    "DATE": 1.2, "TIME": 1.2, "MONEY": 1.2,  # "next Tuesday", "at eight", "fifty bucks"
}

# Zipf scale: log10 of occurrences per billion words ("the" 7.7, "food" 5.4,
# "telescope" 3.75, unknown 0). Rarity = how far below MAX_ZIPF, at most MAX_RARITY.
MAX_ZIPF = 7.0
MAX_RARITY = 4.0
RARITY_GAIN = 0.3
# Every noun of everyday talk passes (zipf below ~6.3); verbs and adjectives only
# when rare (zipf below ~3.7).
MIN_WEIGHT = 1.2
# Single words English has (almost) never seen are nearly always misheard ("pung",
# "bellad"), and their rarity would make them look important.
KNOWN_ZIPF = 1.0

# Nouns without content, and spoken fillers spaCy's stop list doesn't know
# (compared with both the word and its lemma, so "lots" -> "lot" is caught).
VAGUE = frozenset("""
    lot bunch kind sort type way thing stuff bit couple number part time times guy
    people person someone somebody something anything everything nothing one
    point fact case end side
    actually anyway basically gonna gotta kinda literally mhm oh ok okay right
    sorta uh uhm um wanna yeah yep
""".split())


def keywords(text: str, final: bool = True) -> list[Keyword]:
    """Every word of `text` worth showing, in spoken order (repeats included).

    final=False: the line is still being spoken, its last word may change.
    """
    doc = NLP(text)
    words = [t.i for t in doc if not (t.is_punct or t.is_space)]
    unsettled = words[-1] if words and not final else None

    entities = [ent for ent in doc.ents if ent.label_ in ENTITY_KIND]
    in_entity = {token.i for ent in entities for token in ent}
    found = [(ent.start, _entity(ent)) for ent in entities
             if unsettled is None or ent.end <= unsettled]
    found += [(token.i, _word(token)) for token in doc
              if token.i not in in_entity and token.i != unsettled
              and token.pos_ in KIND and not token.is_stop and not _vague(token)]
    return [k for _, k in sorted(found, key=lambda f: f[0])
            if k and k.weight >= MIN_WEIGHT and _known(k)]


def _entity(ent) -> Keyword | None:
    tokens = [t for t in ent if not (t.is_stop or t.is_punct)]  # "the United States"
    if not tokens:
        return None
    phrase = ent.doc[tokens[0].i: tokens[-1].i + 1].text
    rarity = max(_rarity(t.lower_) for t in tokens)
    return Keyword(phrase.lower(), phrase, ENTITY_KIND[ent.label_] * (1 + RARITY_GAIN * rarity))


def _word(token) -> Keyword:
    key = token.lemma_.lower() if token.pos_ != "PROPN" else token.lower_
    weight = KIND[token.pos_] * (1 + RARITY_GAIN * _rarity(token.lower_))
    return Keyword(key, token.text, weight)


def _known(k: Keyword) -> bool:
    return " " in k.text or zipf_frequency(k.text.lower(), "en") >= KNOWN_ZIPF


def _vague(token) -> bool:
    return token.lower_ in VAGUE or token.lemma_.lower() in VAGUE


def _rarity(word: str) -> float:
    return min(max(0.0, MAX_ZIPF - zipf_frequency(word, "en")), MAX_RARITY)
