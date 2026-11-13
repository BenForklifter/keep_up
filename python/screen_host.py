"""Shows the live transcript on the monitor attached to the board.

Runs directly on the board (not in the app container, which has no access to
the display): deploy.sh starts it with the board's own python3. main.py in the
container writes the text to .screen/text in the app folder, which both sides see.
"""

import time
from pathlib import Path

from screen import Screen

TEXT_FILE = Path(__file__).resolve().parents[1] / ".screen/text"


def main():
    screen = Screen()
    screen.show("Starting ...")
    last = None
    while True:
        try:
            mtime = TEXT_FILE.stat().st_mtime_ns
            if mtime != last:
                last = mtime
                screen.show(TEXT_FILE.read_text())
        except FileNotFoundError:
            pass
        time.sleep(0.05)


if __name__ == "__main__":
    main()
