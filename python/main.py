"""keep_up: the key words of a dinner conversation on a display on the table.

Entry point of the App Lab app; README.md describes the whole system. This module
wires the parts together and handles the Bridge calls from the sketch:

    knob_turned(steps)  more or fewer key words, or the live transcript at the top
    knob_pressed()      jump between the single most important word and the transcript
    moved(milli_g)      the table was bumped: someone is there

An input that wakes keep_up from its sleep mode (presence.py) does nothing else.
"""

import threading
import time

from arduino.app_utils import App, Bridge

from display import Display
from microphone import TableMic
from presence import Presence
from shared import read_names, touched_at
from speech import SpeechToText
from stats import SystemStats

REFRESH_SECONDS = 0.25  # how often the screen is updated (fading, dwell times, names)
STATS_SECONDS = 5.0  # how often CPU and RAM are measured for the screen corner ...
STATS_LOG_SECONDS = 60.0  # ... and how often they go to the app log
WAKE_CHECK_SECONDS = 60.0
NO_MIC_RETRY_SECONDS = 60.0

LISTENING = "Listening ..."

display = Display()
stt: SpeechToText | None = None  # once the speech model is loaded
mic: TableMic | None = None  # open while awake


def go_to_sleep() -> None:
    display.sleep()
    if mic is not None:
        mic.close()  # ends the speech recognition in loop()


def wake_up() -> None:
    display.wake(LISTENING)  # loop() opens the microphone again


presence = Presence(on_sleep=go_to_sleep, on_wake=wake_up)


def open_mic() -> TableMic | None:
    try:
        new_mic = TableMic()
    except Exception as e:
        print(f"mic: none found ({e!r})")
        return None
    print(f"mic: {new_mic.name}")
    return new_mic


def on_line(line_id: int, text: str, complete: bool) -> None:
    presence.seen("speech")
    display.heard(line_id, text, complete)


def on_knob_turned(steps: int) -> None:
    print(f"knob: turned {steps}")
    if not presence.seen("knob"):
        display.turn(int(steps))


def on_knob_pressed() -> None:
    print("knob: pressed")
    if not presence.seen("knob"):
        display.press()


def on_moved(milli_g: int) -> None:
    presence.seen(f"table bumped ({milli_g} mg)")


def refresh_forever() -> None:
    touched = touched_at()
    while True:
        time.sleep(REFRESH_SECONDS)
        if (now_touched := touched_at()) != touched:
            touched = now_touched
            presence.seen("screen tapped")
        presence.check()
        if not presence.awake:
            continue  # nothing moves on the dark screen
        names = read_names()  # can be changed on the screen at any time
        display.watch_for(names)
        if stt is not None:
            stt.listen_for(names)
        display.tick()


def measure_forever() -> None:
    stats, last_log = SystemStats(), time.monotonic()
    while True:
        time.sleep(STATS_SECONDS)
        stats.sample()
        display.set_info(stats.label())  # also while asleep, for a monitor left on
        if time.monotonic() - last_log >= STATS_LOG_SECONDS:
            last_log = time.monotonic()
            print(stats.log_line() + ("" if presence.awake else " (asleep)"))


def loop() -> None:
    """Listen while awake; the microphone is opened anew after each sleep."""
    global mic
    if not presence.wait_awake(timeout=WAKE_CHECK_SECONDS):
        return
    mic = open_mic()
    if mic is None:
        display.message("No microphone - plug one in")
        time.sleep(NO_MIC_RETRY_SECONDS)
        return
    if presence.awake:  # it may have fallen asleep while the microphone opened
        display.message(LISTENING)
        try:
            stt.run(mic.blocks())  # returns once the microphone stops: asleep, unplugged
        except Exception as e:
            print(f"speech: stopped ({e!r})")
    mic.close()
    if presence.awake:
        print("speech: restarting")
        time.sleep(1)


Bridge.provide("knob_turned", on_knob_turned)
Bridge.provide("knob_pressed", on_knob_pressed)
Bridge.provide("moved", on_moved)
threading.Thread(target=refresh_forever, daemon=True).start()
threading.Thread(target=measure_forever, daemon=True).start()

display.message("Loading speech model ...")
try:
    stt = SpeechToText(on_line)  # the first start downloads the model: needs internet once
except Exception:
    display.message("Speech model failed to load - see app logs")
    raise

App.run(user_loop=loop)
