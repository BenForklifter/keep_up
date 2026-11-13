"""Runs directly on the board, next to the app container, for the hardware the
container cannot reach: the monitor and the reSpeaker's USB controls.

deploy.sh starts it with the board's own python3 (standard library + GTK only).

- Screen: draws the frames the app writes to .board/screen.json.
- Mic array: follows who is speaking with the reSpeaker XVF3800 (mic_array.py)
  and reports it to the app in .board/mic.json.
- Both: a blue glow at the screen edge points towards whoever is speaking. It goes a
  few seconds after they stop, and is off while the array is muted. Which way
  it points is set on the screen itself: tap it (screen.py).
- When the app hears someone say a name of the person at the screen (also set on
  the screen), the screen shows that they are being talked to.
- Sleep mode (the app's frame says "asleep", presence.py): the screen fades to
  black, then the monitor goes to standby (monitor.py); the mic array's ring goes
  off and it is no longer polled. A tap on the screen wakes the app
  (.board/touch.json, screen.py); waking, the monitor comes on first, then the
  picture fades in.
"""

import signal
import sys
import threading
import time

from mic_array import SpeakerFollower
from monitor import Monitor
from screen import Screen
from shared import SCREEN_FILE, read_json

POLL_SECONDS = 0.05
# Asleep, the monitor itself goes to standby: on the 7" Elecrow that saves ~2 of its
# 2.5 W. False: the screen only goes dark, with "Asleep" and CPU / RAM at the bottom.
MONITOR_OFF = True


def open_monitor() -> Monitor | None:
    if not MONITOR_OFF:
        return None
    try:
        monitor = Monitor()
        monitor.power(True)  # in case it was left off
        return monitor
    except OSError as e:
        print(f"Monitor stays on while asleep, only dark: {e}")
        return None


def show_forever(screen: Screen, follower: SpeakerFollower, monitor: Monitor | None) -> None:
    """Draw the app's frames as they come, and point the glow at the speaker."""
    last_frame, last_direction, asleep, monitor_off = None, None, False, False
    while True:
        try:
            changed = SCREEN_FILE.stat().st_mtime_ns
        except FileNotFoundError:
            changed = None
        if changed != last_frame:
            last_frame = changed
            frame = read_json(SCREEN_FILE)
            if frame:
                screen.show(frame.get("markup", ""), frame.get("status", ""), markup=True,
                            info=frame.get("info", ""))
                screen.addressed(bool(frame.get("addressed")))
                if bool(frame.get("asleep")) != asleep:
                    asleep = not asleep
                    if monitor_off:  # waking: the monitor first, the picture fades in
                        monitor.power(True)
                        monitor_off = False
                    screen.sleep(asleep)
                    follower.sleep(asleep)
        if asleep and monitor and not monitor_off and screen.dark:
            monitor.power(False)
            monitor_off = True

        state = follower.state
        direction = None if state.muted or asleep else state.direction
        if direction != last_direction:
            last_direction = direction
            screen.point_to(direction)
        time.sleep(POLL_SECONDS)


def main():
    screen = Screen()  # before Monitor(): GTK's X error handler then ignores its errors (no exit)
    screen.show("Starting ...")
    monitor = open_monitor()
    follower = SpeakerFollower()
    threading.Thread(target=follower.run_forever, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # deploy.sh stop: clean up below
    try:
        show_forever(screen, follower, monitor)
    finally:
        follower.leds_off()
        if monitor:
            monitor.power(True)  # not left dark after deploy.sh stop


if __name__ == "__main__":
    main()
