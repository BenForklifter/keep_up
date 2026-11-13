"""The keyword board: the N key words of the conversation, following the talk.

Every keyword that is said becomes a candidate with a strength

    strength = weight * (1 + REPEAT_BOOST * echo) * 0.5 ** (seconds since last said / half-life)
    echo     = sum over its earlier mentions of 0.5 ** (seconds since that mention / TOPIC_MEMORY)

and the N strongest are on the board. So a new word pushes out the weakest one
right away, an important word outlives a newer ordinary one, and a word that
keeps coming up gets stronger with every mention. Only recent mentions count
(echo fades), so when the conversation moves on, the old topic clears quickly.
In silence all words fade alike, so the board keeps the last topic.

The half-life grows with N: a single word follows the talk closely (the most
important of the last second or two), eight words sum up the last few sentences.

For reading at a glance, words don't move: each keeps its slot until it is
replaced, and stays at least MIN_DWELL seconds once shown.

Pure logic: the caller passes the time, so it can be tested with made-up clocks.
"""

from collections import Counter
from dataclasses import dataclass

from keywords import Keyword

HALF_LIFE_PER_SLOT = 1.0  # seconds until a word counts half as much as when said, per word shown
MIN_DWELL = 0.8  # seconds a word stays on the board at least (time to read it)
REPEAT_BOOST = 0.6  # extra strength per recent earlier mention ...
TOPIC_MEMORY = 10.0  # ... which counts half after this many seconds
ECHO_HORIZON = 4 * TOPIC_MEMORY  # older mentions are forgotten
FORGET_AFTER = 300.0  # seconds after which a word that is not shown is dropped
TRACKED_LINES = 20  # recent lines whose keywords are remembered (to count each once)


@dataclass(frozen=True)
class Shown:
    text: str
    age: float  # seconds since it was last said
    direction: int | None  # where its speaker sits (None: unknown)


@dataclass
class _Candidate:
    text: str
    weight: float
    direction: int | None
    said: list[float]  # when it was said, oldest first

    @property
    def said_at(self) -> float:
        return self.said[-1]

    def strength(self, now: float, half_life: float) -> float:
        *earlier, last = self.said
        echo = sum(0.5 ** ((now - t) / TOPIC_MEMORY) for t in earlier)
        return self.weight * (1 + REPEAT_BOOST * echo) * 0.5 ** ((now - last) / half_life)


@dataclass(frozen=True)
class _Slot:
    key: str
    shown_at: float


class KeywordBoard:
    def __init__(self, size: int):
        self._candidates: dict[str, _Candidate] = {}
        self._lines: dict[int, Counter] = {}  # keyword counts per recent line, oldest first
        self._slots: list[_Slot | None] = [None] * size

    def hear(self, line_id: int, found: list[Keyword], now: float,
             direction: int | None = None) -> None:
        """The text of a speech line changed; `found` are all its keywords now.

        Lines are revised while they are spoken: keywords seen before in the same
        line are not counted again, keywords revised away are taken back.
        `direction`: where the speaker sits, if known; a word belongs to whoever said it last.
        """
        before = self._lines.get(line_id, Counter())
        current = Counter(keyword.key for keyword in found)
        latest = {keyword.key: keyword for keyword in found}  # last spelling ("dog" -> "dogs")

        for key in before:
            for _ in range(before[key] - current[key]):
                self._take_back(key)
        for key, count in current.items():
            if key in self._candidates:
                self._candidates[key].text = latest[key].text
            for _ in range(count - before[key]):
                self._mention(latest[key], now, direction)

        self._lines[line_id] = current
        for old in list(self._lines)[:-TRACKED_LINES]:
            del self._lines[old]

    def resize(self, size: int, now: float) -> None:
        """Show `size` words; the strongest words already shown keep their place."""
        shown = [slot for slot in self._slots if slot and slot.key in self._candidates]
        strongest = sorted(shown, key=lambda s: self._strength(s.key, now), reverse=True)[:size]
        kept = [slot for slot in shown if slot in strongest]
        self._slots = kept + [None] * (size - len(kept))

    def words(self, now: float) -> list[Shown | None]:
        """The board right now, slot by slot (None = empty slot)."""
        self._arrange(now)
        return [self._shown(s.key, now) if s else None for s in self._slots]

    def _shown(self, key: str, now: float) -> Shown:
        candidate = self._candidates[key]
        return Shown(candidate.text, now - candidate.said_at, candidate.direction)

    def _arrange(self, now: float) -> None:
        shown = {slot.key for slot in self._slots if slot}
        for key in [k for k, c in self._candidates.items()
                    if now - c.said_at > FORGET_AFTER and k not in shown]:
            del self._candidates[key]

        ranked = sorted(self._candidates, key=lambda k: self._strength(k, now), reverse=True)
        wanted = ranked[:len(self._slots)]

        # Free the slots of words that were revised away, or that lost their
        # place and have been readable long enough.
        for i, slot in enumerate(self._slots):
            if slot and (slot.key not in self._candidates or
                         (slot.key not in wanted and now - slot.shown_at >= MIN_DWELL)):
                self._slots[i] = None

        shown = {slot.key for slot in self._slots if slot}
        newcomers = [key for key in wanted if key not in shown]  # strongest first
        free = [i for i, slot in enumerate(self._slots) if slot is None]
        for i, key in zip(free, newcomers):
            self._slots[i] = _Slot(key, now)

    def _strength(self, key: str, now: float) -> float:
        return self._candidates[key].strength(now, HALF_LIFE_PER_SLOT * len(self._slots))

    def _mention(self, keyword: Keyword, now: float, direction: int | None) -> None:
        candidate = self._candidates.get(keyword.key)
        if candidate is None:
            self._candidates[keyword.key] = _Candidate(keyword.text, keyword.weight, direction, [now])
            return
        candidate.weight = max(candidate.weight, keyword.weight)
        candidate.direction = direction
        candidate.said = [t for t in candidate.said if now - t < ECHO_HORIZON] + [now]

    def _take_back(self, key: str) -> None:
        candidate = self._candidates.get(key)
        if candidate is None:
            return
        candidate.said.pop()
        if not candidate.said:
            del self._candidates[key]
