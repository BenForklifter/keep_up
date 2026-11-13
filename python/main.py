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
"""

import threading
import time

from arduino.app_utils import App, Bridge

from display import Display
from microphone import TableMic
from sci import CarbonMeter
from shared import read_names

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

display = Display()
stt: SpeechToText | None = None  # once the speech model is loaded


def open_mic() -> TableMic | None:
    try:
        mic = TableMic()
        print(f"Microphone: {mic.name}")
        return mic
    except Exception as e:
        print(f"No microphone ({e!r})")
        return None


def on_line(line_id: int, text: str, complete: bool) -> None:
    display.heard(line_id, text, complete)


def on_knob_turned(steps) -> None:
    print(f"knob: turned {steps}")
    display.turn(int(steps))


def on_knob_pressed() -> None:
    print("knob: pressed")
    display.press()


def refresh_forever():
    while True:
        time.sleep(REFRESH_SECONDS)
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
        display.set_info(meter.label())
        if time.monotonic() - last_log >= SCI_LOG_SECONDS:
            last_log = time.monotonic()
            print(meter.log_line())


Bridge.provide("knob_turned", on_knob_turned)
Bridge.provide("knob_pressed", on_knob_pressed)
threading.Thread(target=refresh_forever, daemon=True).start()
threading.Thread(target=measure_forever, daemon=True).start()

display.message("Loading speech model ...")
try:
    stt = SpeechToText(on_line)  # first start: downloads the model, needs internet once
except Exception:
    display.message("Speech model failed to load - see app logs")
    raise
mic = open_mic()
display.message("Listening ..." if mic is not None else "No microphone - plug one in and redeploy")


def loop():
    if mic is None:
        time.sleep(60)
        return
    try:
        stt.run(mic.blocks())  # returns only when the microphone stream ends
    except Exception as e:
        print(f"Speech recognition stopped: {e!r}")
    print("Restarting speech recognition")
    time.sleep(1)


App.run(user_loop=loop)
