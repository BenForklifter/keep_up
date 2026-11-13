"""keep_up: the key words of a dinner conversation on a display on the table.

Against Dinner Table Syndrome: at a table where everyone talks at once, people
who are deaf or hard of hearing lose the thread. A glance at the display shows
the topic and what was just said.

The reSpeaker XVF3800 mic array in the middle of the table (or any USB
microphone, microphone.py) feeds Moonshine (offline speech recognition on the
board's CPU, speech.py). The keyword board (board.py) keeps the N most important
words of the talk; the Modulino Knob on the sketch side picks N, or the full live
transcript (display.py). Turning sends "knob_turned", pressing "knob_pressed"
over the Bridge. A blue glow at the screen edge points towards whoever is speaking
(mic array, board_host.py).
When someone says a name of the person at the screen (set on the screen itself,
screen.py), the screen shows that they are being talked to; speech recognition
listens for these names in particular.
The bottom-right corner shows the estimated power and carbon footprint (sci.py).

When nobody has been at the table for a while, keep_up sleeps to save power:
microphone and speech recognition off, monitor in standby (board_host.py). Speech
would not wake it then; a tap on the screen, a bump of the table (the Modulino
Movement on the sketch side sends "moved") or the knob does (presence.py). A knob
turn or press that wakes it does nothing else.
"""

import threading
import time

from arduino.app_utils import App, Bridge

from display import Display
from microphone import TableMic, test_audio_waiting
from presence import Presence
from sci import CarbonMeter
from shared import read_names, touched_at

# Speech recognizer: "moonshine" (speech.py) or "kroko" (speech_kroko.py). On a laptop
# benchmark Kroko was clearly more accurate and lighter (tools/eval/results/); switch
# once confirmed on the board.
ASR = "kroko"
if ASR == "kroko":
    from speech_kroko import SpeechToText
else:
    from speech import SpeechToText

REFRESH_SECONDS = 0.25  # how often the board is re-arranged (fading, dwell times)
SCI_SAMPLE_SECONDS = 5.0  # how often power is estimated and the corner updated
SCI_LOG_SECONDS = 60.0  # how often the carbon figures go to the app log
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
        print(f"Microphone: {new_mic.name}")
        return new_mic
    except Exception as e:
        print(f"No microphone ({e!r})")
        return None


def on_line(line_id: int, text: str, complete: bool) -> None:
    presence.seen("speech")
    display.heard(line_id, text, complete)


def on_knob_turned(steps) -> None:
    print(f"knob: turned {steps}")
    if not presence.seen("knob"):
        display.turn(int(steps))


def on_knob_pressed() -> None:
    print("knob: pressed")
    if not presence.seen("knob"):
        display.press()


def on_moved(milli_g) -> None:
    presence.seen(f"table bumped ({milli_g} mg)")


def refresh_forever():
    touched = touched_at()
    while True:
        time.sleep(REFRESH_SECONDS)
        if (now_touched := touched_at()) != touched:
            touched = now_touched
            presence.seen("screen tapped")
        if not presence.awake and test_audio_waiting():
            presence.seen("test audio (tools/eval)")  # played through the microphone
        presence.check()
        if not presence.awake:
            continue  # nothing moves on the dark screen
        names = read_names()  # can be changed on the screen at any time
        display.watch_for(names)
        if stt is not None:
            stt.listen_for(names)
        display.tick()


def measure_forever():
    meter, last_log = CarbonMeter(), time.monotonic()
    while True:
        time.sleep(SCI_SAMPLE_SECONDS)
        meter.sample()
        display.set_info(meter.label())  # also while asleep (with the monitor on: dark screen)
        if time.monotonic() - last_log >= SCI_LOG_SECONDS:
            last_log = time.monotonic()
            print(meter.log_line() + ("" if presence.awake else " (asleep)"))


Bridge.provide("knob_turned", on_knob_turned)
Bridge.provide("knob_pressed", on_knob_pressed)
Bridge.provide("moved", on_moved)
threading.Thread(target=refresh_forever, daemon=True).start()
threading.Thread(target=measure_forever, daemon=True).start()

display.message("Loading speech model ...")
try:
    stt = SpeechToText(on_line)  # first start: downloads the model, needs internet once
except Exception:
    display.message("Speech model failed to load - see app logs")
    raise


def loop():
    """Listen while awake; the microphone is opened anew after each sleep."""
    global mic
    if not presence.wait_awake(timeout=60):
        return  # asleep: nothing to do
    mic = open_mic()
    if mic is None:
        display.message("No microphone - plug one in")
        time.sleep(NO_MIC_RETRY_SECONDS)  # then look again
        return
    if presence.awake:  # else it fell asleep while the microphone opened
        display.message(LISTENING)
        try:
            stt.run(mic.blocks())  # returns once the microphone stops: asleep, unplugged
        except Exception as e:
            print(f"Speech recognition stopped: {e!r}")
    mic.close()
    if presence.awake:
        print("Restarting speech recognition")
        time.sleep(1)


App.run(user_loop=loop)
