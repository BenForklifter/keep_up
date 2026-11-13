"""Find the words that carry a conversation: the things, names, places and plans.

Two signals decide how much a word is worth:

- What it is. spaCy (en_core_web_sm) tags parts of speech and finds named
  entities. Nouns and names carry the topic, verbs and adjectives sometimes do
  ("divorced", "pregnant"), words like "honestly" or "went" never do. Names are
  kept whole ("New York", "Aunt Mary").
- How informative it is. A word is worth more the rarer it is in everyday
  English (wordfreq: frequencies measured over billions of words, in effect an
  IDF score). "telescope" says more than "thing".

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
    weight: float  # how much it tells about the conversation, ~2 ... 7


# The parser is not needed (no syntax trees), skipping it saves most of the CPU.
NLP = spacy.load("en_core_web_sm", exclude=["parser"])

# Zipf scale: log10 of occurrences per billion words. "the" ~7.7, "telescope" ~3.6,
# unknown words 0. Rarity = how far below MAX_ZIPF a word is, at most MAX_RARITY:
# beyond "rare" (zipf 2, e.g. "telescope" is 3.6) words are not more important,
# and words wordfreq has never seen are often misheard ones.
MAX_ZIPF = 7.0
MAX_RARITY = 5.0
MIN_WEIGHT = 1.8  # weaker words are never shown

POS_WEIGHT = {"NOUN": 1.0, "PROPN": 1.0, "VERB": 0.6, "ADJ": 0.6}
ENTITY_BONUS = {
    "PERSON": 2.0, "GPE": 2.0, "LOC": 2.0, "FAC": 2.0, "ORG": 1.5, "NORP": 1.5,
    "PRODUCT": 1.5, "EVENT": 1.5, "WORK_OF_ART": 1.5, "LANGUAGE": 1.5, "LAW": 1.0,
    "DATE": 1.0, "TIME": 1.0, "MONEY": 1.0,  # "next Tuesday", "at eight", "fifty bucks"
}

# Spoken fillers spaCy's stop list doesn't know.
FILLERS = frozenset("""
    actually anyway basically guy guys gonna gotta kind kinda lot literally
    mhm oh ok okay right sort sorta stuff thing things uh uhm um wanna yeah yep
""".split())


def keywords(text: str) -> list[Keyword]:
    """Every word of `text` worth showing, in spoken order (repeats included)."""
    doc = NLP(text)
    entities = [ent for ent in doc.ents if ent.label_ in ENTITY_BONUS]
    in_entity = {token.i for ent in entities for token in ent}
    found = [(ent.start, _entity(ent)) for ent in entities]
    found += [(token.i, _word(token)) for token in doc
              if token.i not in in_entity and token.pos_ in POS_WEIGHT
              and not token.is_stop and token.lower_ not in FILLERS]
    return [k for _, k in sorted(found, key=lambda f: f[0]) if k and k.weight >= MIN_WEIGHT]


def _entity(ent) -> Keyword | None:
    tokens = [t for t in ent if not (t.is_stop or t.is_punct)]  # "the United States"
    if not tokens:
        return None
    phrase = ent.doc[tokens[0].i: tokens[-1].i + 1].text
    weight = max(_rarity(t.lower_) for t in tokens) + ENTITY_BONUS[ent.label_]
    return Keyword(phrase.lower(), phrase, weight)


def _word(token) -> Keyword:
    key = token.lemma_.lower() if token.pos_ != "PROPN" else token.lower_
    return Keyword(key, token.text, POS_WEIGHT[token.pos_] * _rarity(token.lower_))


def _rarity(word: str) -> float:
    return min(max(0.0, MAX_ZIPF - zipf_frequency(word, "en")), MAX_RARITY)
