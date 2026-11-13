"""Runs on the board itself, next to the app container, for the hardware the
container cannot reach: the monitor and the reSpeaker's USB controls.

deploy.sh starts it with the board's own python3 (standard library and GTK only).

- Screen (screen.py): draws the frames the app writes to .board/screen.json, with
  a glow at the screen edge towards whoever is speaking.
- Mic array (mic_array.py): follows who is speaking and reports it to the app in
  .board/mic.json.
- Sleep mode: when the app's frame says "asleep", the screen fades to black, then
  the monitor goes to standby (monitor.py) and the mic array is left alone. Waking
  up, the monitor comes on first, then the picture fades in.
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
# Asleep, put the monitor itself into standby: on the 7" Elecrow that saves ~2 of its
# 2.5 W. False: the screen only goes dark, showing "Asleep" and the CPU and RAM figures.
MONITOR_OFF = True


def open_monitor() -> Monitor | None:
    if not MONITOR_OFF:
        return None
    try:
        monitor = Monitor()
    except OSError as e:
        print(f"monitor: stays on while asleep, only dark ({e})")
        return None
    monitor.power(True)  # in case it was left off
    return monitor


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
                screen.show(
                    frame.get("markup", ""),
                    frame.get("status", ""),
                    markup=True,
                    info=frame.get("info", ""),
                )
                screen.addressed(bool(frame.get("addressed")))
                if bool(frame.get("asleep")) != asleep:
                    asleep = not asleep
                    if monitor_off:  # waking: the monitor first, then the picture fades in
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


def main() -> None:
    screen = Screen()  # before Monitor(): GTK's X error handler then ignores its errors
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
