"""Sleep mode: keep_up goes dark when nobody has been at the table for a while.

While asleep, the microphone and speech recognition are off (main.py), which is
most of the board's CPU load; the screen fades to black and the monitor goes to
standby (screen.py, monitor.py); the mic array's LED ring is off and the array is
no longer polled (mic_array.py).

Signs that someone is at the table keep it awake: speech, a tap on the screen, a
bump of the table, the knob. All of them but speech (the microphone is off) also
wake it up again.

    presence = Presence(on_sleep=..., on_wake=...)
    presence.seen("knob")   # someone is there; wakes up if asleep
    presence.check()        # call regularly: falls asleep after SLEEP_AFTER seconds
"""

import threading
import time
from collections.abc import Callable

SLEEP_AFTER = 8.0  # seconds without a sign of anyone; short for demos, ~300 at a real table


class Presence:
    def __init__(self, on_sleep: Callable[[], None], on_wake: Callable[[], None]) -> None:
        self._on_sleep, self._on_wake = on_sleep, on_wake
        self._awake = threading.Event()
        self._awake.set()
        self._last_seen = time.monotonic()
        self._lock = threading.Lock()  # one change at a time, its callback included

    @property
    def awake(self) -> bool:
        return self._awake.is_set()

    def wait_awake(self, timeout: float) -> bool:
        """Wait at most `timeout` seconds until awake; returns whether it is."""
        return self._awake.wait(timeout)

    def seen(self, sign: str) -> bool:
        """Someone is there; `sign` says how ("speech", "knob", ...). Any thread.

        Returns True if this woke keep_up up: then the input should do nothing
        else (a knob turn that wakes the screen doesn't also change what it shows).
        """
        with self._lock:
            self._last_seen = time.monotonic()
            if self._awake.is_set():
                return False
            print(f"presence: awake ({sign})")
            self._awake.set()
            self._on_wake()
            return True

    def check(self) -> None:
        """Fall asleep once nobody has been seen for SLEEP_AFTER seconds."""
        with self._lock:
            if self._awake.is_set() and time.monotonic() - self._last_seen >= SLEEP_AFTER:
                print(f"presence: asleep after {SLEEP_AFTER:g} s without anyone")
                self._awake.clear()
                self._on_sleep()
