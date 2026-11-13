"""Runs directly on the board, next to the app container, for the hardware the
container cannot reach: the monitor and the reSpeaker's USB controls.

deploy.sh starts it with the board's own python3 (standard library + GTK only).

- Screen: draws the frames the app writes to .board/screen.json.
- Mic array: follows who is speaking with the reSpeaker XVF3800 (mic_array.py)
  and reports it to the app in .board/mic.json.
"""

import signal
import sys
import threading
import time

from mic_array import SpeakerFollower
from screen import Screen
from shared import SCREEN_FILE, read_json

POLL_SECONDS = 0.05


def show_frames_forever(screen: Screen) -> None:
    last = None
    while True:
        try:
            changed = SCREEN_FILE.stat().st_mtime_ns
        except FileNotFoundError:
            changed = None
        if changed != last:
            last = changed
            frame = read_json(SCREEN_FILE)
            if frame:
                screen.show(frame.get("markup", ""), frame.get("status", ""), markup=True,
                            info=frame.get("info", ""))
        time.sleep(POLL_SECONDS)


def main():
    screen = Screen()
    screen.show("Starting ...")
    follower = SpeakerFollower()
    threading.Thread(target=follower.run_forever, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # deploy.sh stop: clean up below
    try:
        show_frames_forever(screen)
    finally:
        follower.leds_off()


if __name__ == "__main__":
    main()
