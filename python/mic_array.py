"""Follows who is speaking at the table with the reSpeaker XVF3800 (runs on the board).

The array estimates the direction of the voice it hears (0..359 degrees) and
whether it is speech. From that this module keeps the current speaker's
direction: the 30-degree sector (one LED of the ring) that recent speech came
from most, recent samples counting more. It then

- lights the LED ring towards the speaker, in the color of their seat
  (the array's own direction-of-arrival LED effect, recolored per speaker),
- reports direction and mute button to the app (shared.MicState), which marks
  each word with that color.

Unplugging and replugging the array is fine: it is looked for every few seconds.
"""

import time
from collections import deque

from shared import NO_MIC_ARRAY, MicState, speaker_color, write_mic_state
from xvf3800 import LED_EFFECT_DOA, LED_EFFECT_OFF, MUTE_OUTPUT, XVF3800

POLL_SECONDS = 0.1
SECTOR = 30  # degrees per LED (12 LEDs)
SPEAKER_WINDOW = 4.0  # seconds of speech the speaker direction is taken from ...
SPEAKER_HALF_LIFE = 1.5  # ... the newest counting most
LED_RING_COLOR = 0x000000  # the LEDs not pointing at the speaker
HEARTBEAT_SECONDS = 1.0  # unchanged state is still re-written this often (app checks age)
RETRY_SECONDS = 5.0


class SpeakerFollower:
    def __init__(self):
        self._array: XVF3800 | None = None
        self._speech = deque()  # (time, direction) of recent speech
        self._state = NO_MIC_ARRAY
        self._written_at = 0.0
        self._led_direction = None

    def run_forever(self) -> None:
        reported = None
        while True:
            try:
                with XVF3800.open() as array:
                    self._array = array
                    major, minor, patch = array.read("VERSION")
                    print(f"reSpeaker XVF3800 found, firmware {major}.{minor}.{patch}")
                    reported = None
                    array.write("LED_EFFECT", [LED_EFFECT_DOA])
                    self._follow(array)
            except OSError as e:  # not plugged in, no permission, unplugged
                problem = _explain(e)
                if problem != reported:
                    print(problem)
                    reported = problem
            finally:
                self._array = None
                self._led_direction = None
                write_mic_state(NO_MIC_ARRAY)
            time.sleep(RETRY_SECONDS)

    def leds_off(self) -> None:
        """Turn the ring off (on shutdown)."""
        try:
            if self._array:
                self._array.write("LED_EFFECT", [LED_EFFECT_OFF])
        except OSError:
            pass

    def _follow(self, array: XVF3800) -> None:
        while True:
            angle, speech = array.read("DOA_VALUE")
            muted = array.read("GPO_READ_VALUES")[MUTE_OUTPUT] == 1
            now = time.monotonic()
            if speech:
                self._speech.append((now, angle))
            direction = self._speaker_direction(now)

            if direction is not None and direction != self._led_direction:
                array.write("LED_DOA_COLOR", [LED_RING_COLOR, speaker_color(direction)])
                self._led_direction = direction
            self._report(MicState(direction, muted), now)
            time.sleep(POLL_SECONDS)

    def _speaker_direction(self, now: float) -> int | None:
        while self._speech and self._speech[0][0] < now - SPEAKER_WINDOW:
            self._speech.popleft()
        votes = {}
        for heard_at, angle in self._speech:
            sector = round(angle / SECTOR) % (360 // SECTOR)
            votes[sector] = votes.get(sector, 0) + 0.5 ** ((now - heard_at) / SPEAKER_HALF_LIFE)
        return max(votes, key=votes.get) * SECTOR if votes else None

    def _report(self, state: MicState, now: float) -> None:
        if state != self._state or now - self._written_at >= HEARTBEAT_SECONDS:
            write_mic_state(state)
            self._state, self._written_at = state, now


def _explain(error: OSError) -> str:
    if isinstance(error, FileNotFoundError):
        return "No reSpeaker XVF3800 plugged in (looking again every few seconds)"
    if isinstance(error, PermissionError):
        return "No access to the reSpeaker's USB controls - run ./deploy.sh setup once"
    return f"reSpeaker XVF3800: {error!r}"
