"""What the monitor on the table shows.

The knob picks a level: the 1, 2, 3, 4, 6 or 8 key words of the conversation
(the keyword board, see board.py) or, at the top, LIVE: the running transcript.
The level is shown small in the bottom-left corner.

Fresh words are black and turn gray as they age, so a glance tells what was
just said and what is the topic from before. In the transcript the line being
spoken is black, earlier lines gray.

Frames go to .screen/text as JSON {"markup", "status"} (Pango markup);
python/screen_host.py, running on the board itself, draws them. Thread-safe:
speech, knob and the refresh timer call in from different threads.
"""

import json
import os
import textwrap
import threading
import time
from html import escape
from pathlib import Path

from board import KeywordBoard, Shown
from keywords import keywords

SCREEN_FILE = Path(__file__).resolve().parents[1] / ".screen/text"
LEVELS = (1, 2, 3, 4, 6, 8)  # number of key words per knob step
LIVE = len(LEVELS)  # the step above: full transcript
START_LEVEL = LEVELS.index(3)

FRESH_SECONDS = 4.0  # words this recent are black ...
FADED_SECONDS = 40.0  # ... and fade to FADED_GRAY over this time
FADED_GRAY = 0.6  # 0 = black, 1 = white
FADE_STEPS = 8  # gray levels used (fewer = fewer redraws)
OLD_LINE_GRAY = 0.55

LINE_CHARS = 24  # transcript: characters per line ...
MAX_LINES = 3  # ... and number of lines (newest text wins)
KEPT_LINES = 10  # transcript lines remembered


class Display:
    def __init__(self):
        self._lock = threading.Lock()
        self._level = START_LEVEL
        self._board = KeywordBoard(LEVELS[START_LEVEL])
        self._lines: dict[int, tuple[str, bool]] = {}  # line id -> (text, complete), oldest first
        self._message = ""  # status text instead of speech ("Listening ...")
        self._frame = None  # last frame written

    # --- input ----------------------------------------------------------------

    def heard(self, line_id: int, text: str, complete: bool) -> None:
        """A line of speech changed (see speech.SpeechToText)."""
        found = keywords(text)  # the slow part, done outside the lock
        with self._lock:
            now = time.monotonic()
            self._message = ""
            self._lines[line_id] = (text, complete)
            for old in list(self._lines)[:-KEPT_LINES]:
                del self._lines[old]
            self._board.hear(line_id, found, now)
            self._render(now)

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
            self._render(time.monotonic())

    def tick(self) -> None:
        """Call a few times a second: lets words fade and expire on the board."""
        with self._lock:
            self._render(time.monotonic())

    # --- output ---------------------------------------------------------------

    def _set_level(self, level: int) -> None:
        self._level = min(max(level, 0), LIVE)
        now = time.monotonic()
        if self._level != LIVE:
            self._board.resize(LEVELS[self._level], now)
        self._render(now)

    def _render(self, now: float) -> None:
        if self._message:
            markup = escape(self._message, quote=False)
        elif self._level == LIVE:
            markup = self._transcript_markup()
        else:
            markup = _board_markup(self._board.words(now))
        status = "LIVE" if self._level == LIVE else str(LEVELS[self._level])
        frame = {"markup": markup, "status": status}
        if frame != self._frame:
            self._frame = frame
            _write_screen(frame)

    def _transcript_markup(self) -> str:
        rows = [(row, complete)
                for text, complete in self._lines.values()
                for row in textwrap.wrap(text, LINE_CHARS)]
        return "\n".join(_colored(row, OLD_LINE_GRAY if complete else 0.0)
                         for row, complete in rows[-MAX_LINES:])


def _board_markup(words: list[Shown | None]) -> str:
    """Up to 3 words one per row, more in two columns. Slots keep their place."""
    columns = 1 if len(words) <= 3 else 2
    cells = [_colored(w.text, _fade(w.age)) for w in words if w]
    rows = ["    ".join(cells[i:i + columns]) for i in range(0, len(cells), columns)]
    return "\n".join(rows)


def _fade(age: float) -> float:
    progress = min(max((age - FRESH_SECONDS) / (FADED_SECONDS - FRESH_SECONDS), 0.0), 1.0)
    return round(progress * FADE_STEPS) / FADE_STEPS * FADED_GRAY


def _colored(text: str, gray: float) -> str:
    level = round(gray * 255)
    return f"<span foreground='#{level:02x}{level:02x}{level:02x}'>{escape(text, quote=False)}</span>"


def _write_screen(frame: dict) -> None:
    """Atomic write, so screen_host.py never reads half a file."""
    SCREEN_FILE.parent.mkdir(exist_ok=True)
    tmp = SCREEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(frame))
    os.replace(tmp, SCREEN_FILE)
