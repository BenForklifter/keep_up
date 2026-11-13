"""What the monitor shows: the live transcript or just its most important words.

The knob picks a level: show the 1, 2, 3, ... most important words of the last
few seconds (refreshed every second), or at the top level the full live transcript.
The level is shown small in the bottom-left corner.

The frame goes to .screen/text as JSON {"text", "status"}; python/screen_host.py
(running on the board itself) draws it. Thread-safe: captions, knob events and
the refresh timer all arrive on different threads.
"""

import json
import os
import textwrap
import threading
import time
from collections import deque
from pathlib import Path

from keywords import important_words

SCREEN_FILE = Path(__file__).resolve().parents[1] / ".screen/text"
WORD_COUNTS = (1, 2, 3, 4, 6, 8)  # knob levels below LIVE
LIVE = len(WORD_COUNTS)  # top level: full transcript
KEYWORD_WINDOW = 8.0  # seconds of speech the important words are picked from
LINE_CHARS = 24  # live transcript: characters per line ...
MAX_LINES = 3  # ... and number of lines (newest text wins)
KEYWORDS_PER_LINE = 3
TRANSCRIPT_CHARS = 500


class Display:
    def __init__(self):
        self._lock = threading.Lock()
        self._level = LIVE
        self._message = ""  # status text instead of captions ("Listening ...")
        self._transcript = ""  # confirmed words, newest at the end
        self._recent = deque()  # (arrival time, confirmed piece) inside KEYWORD_WINDOW
        self._pending = ""  # tentative tail, may still change
        self._keywords = ""  # last important words shown (kept through silence)

    # --- input ----------------------------------------------------------------

    def turn(self, steps: int) -> None:
        """Knob turned: change the level, clamped to 1 word ... LIVE."""
        with self._lock:
            self._set_level(self._level + steps)

    def press(self) -> None:
        """Knob pressed: jump between the single most important word and LIVE."""
        with self._lock:
            self._set_level(0 if self._level == LIVE else LIVE)

    def message(self, text: str) -> None:
        with self._lock:
            self._message = text
            self._render()

    def caption(self, confirmed: str, pending: str) -> None:
        """New ASR event: `confirmed` words to append, `pending` replaces the tail."""
        with self._lock:
            self._message = ""
            if confirmed.strip():
                self._transcript = _join(self._transcript, confirmed)[-TRANSCRIPT_CHARS:]
                self._recent.append((time.monotonic(), confirmed.strip()))
            self._pending = pending.strip()
            if self._level == LIVE:
                self._render()

    def tick(self) -> None:
        """Call about once a second: refreshes the important words."""
        with self._lock:
            if self._level != LIVE and self._update_keywords():
                self._render()

    # --- output ---------------------------------------------------------------

    def _set_level(self, level: int) -> None:
        self._level = min(max(level, 0), LIVE)
        self._update_keywords()
        self._render()

    def _update_keywords(self) -> bool:
        """Re-pick the important words of the last KEYWORD_WINDOW seconds.

        Returns whether they changed. In silence the last ones stay on screen.
        """
        if self._level == LIVE:
            return False
        while self._recent and self._recent[0][0] < time.monotonic() - KEYWORD_WINDOW:
            self._recent.popleft()
        text = " ".join([piece for _, piece in self._recent] + [self._pending])
        words = important_words(text, WORD_COUNTS[self._level])
        if not words:
            return False
        lines = [" ".join(words[i:i + KEYWORDS_PER_LINE])
                 for i in range(0, len(words), KEYWORDS_PER_LINE)]
        keywords = "\n".join(lines)
        changed, self._keywords = keywords != self._keywords, keywords
        return changed

    def _render(self) -> None:
        if self._message:
            text = self._message
        elif self._level == LIVE:
            lines = textwrap.wrap(_join(self._transcript, self._pending), LINE_CHARS)
            text = "\n".join(lines[-MAX_LINES:])
        else:
            text = self._keywords
        status = "LIVE" if self._level == LIVE else str(WORD_COUNTS[self._level])
        _write_screen({"text": text, "status": status})


def _join(a: str, b: str) -> str:
    """Caption pieces come without spaces at the edges ("not" + "the")."""
    a, b = a.rstrip(), b.strip()
    return f"{a} {b}" if a and b else a or b


def _write_screen(frame: dict) -> None:
    """Atomic write, so screen_host.py never reads half a file."""
    SCREEN_FILE.parent.mkdir(exist_ok=True)
    tmp = SCREEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(frame))
    os.replace(tmp, SCREEN_FILE)
