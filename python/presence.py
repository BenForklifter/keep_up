"""Sleep mode: when nobody has been at the table for a while, keep_up goes dark to save power.

While asleep
- the microphone is closed and speech recognition stops (main.py), by far the
  biggest part of the board's CPU load,
- the screen fades to black and the monitor goes to standby (screen.py,
  monitor.py; with board_host.MONITOR_OFF = False it stays on, black except for
  "Asleep" and the CPU / RAM figures, which show how far the load drops),
- the mic array's LED ring is off and the array is no longer polled (mic_array.py).

Anything that shows someone is at the table keeps it awake: something said, a tap
anywhere on the screen, a bump of the table (the accelerometer on the sketch side:
someone sits down, puts down a glass), the knob. All but speech (the microphone
is off) also wake it up again.

    presence = Presence(on_sleep=..., on_wake=...)
    presence.seen("speech")   # someone is there (wakes up if asleep)
    presence.check()          # every fraction of a second: falls asleep when nobody is
"""

import threading
import time
from collections.abc import Callable

SLEEP_AFTER = 8.0  # seconds without anyone: nothing said, no tap, bump or knob.
                   # Demo value, short enough to show in a pitch; at a real table ~5 min.


class Presence:
    def __init__(self, on_sleep: Callable[[], None], on_wake: Callable[[], None]):
        self._on_sleep, self._on_wake = on_sleep, on_wake
        self._awake = threading.Event()
        self._awake.set()
        self._last_seen = time.monotonic()
        self._lock = threading.Lock()  # one change at a time, its callback included

    @property
    def awake(self) -> bool:
        return self._awake.is_set()

    def wait_awake(self, timeout: float) -> bool:
        """Wait until awake, at most `timeout` seconds; whether it is."""
        return self._awake.wait(timeout)

    def seen(self, sign: str) -> bool:
        """Someone is there; `sign` says how ("speech", "knob", ...). Any thread.

        Wakes up if asleep, and then returns True: the input only woke it (a knob
        turn that wakes the screen doesn't also change what it shows).
        """
        with self._lock:
            self._last_seen = time.monotonic()
            if self._awake.is_set():
                return False
            print(f"☀️ awake: {sign}")
            self._awake.set()
            self._on_wake()
            return True

    def check(self) -> None:
        """Call regularly: falls asleep once nobody has been seen for SLEEP_AFTER seconds."""
        with self._lock:
            if self._awake.is_set() and time.monotonic() - self._last_seen >= SLEEP_AFTER:
                print(f"💤 asleep: nobody for {SLEEP_AFTER:g} s")
                self._awake.clear()
                self._on_sleep()
