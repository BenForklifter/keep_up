"""keep_up: the key words of a dinner conversation on a display on the table.

Against Dinner Table Syndrome: at a table where everyone talks at once, people
who are deaf or hard of hearing lose the thread. A glance at the display shows
the topic and what was just said.

The reSpeaker XVF3800 mic array in the middle of the table (or any USB
microphone, microphone.py) feeds Moonshine (offline speech recognition on the
board's CPU, speech.py). The keyword board (board.py) keeps the N most important
words of the talk; the Modulino Knob on the sketch side picks N, or the full live
transcript (display.py). Turning sends "knob_turned", pressing "knob_pressed"
over the Bridge. Who said a word comes from the mic array via board_host.py.
"""

import threading
import time

from arduino.app_utils import App, Bridge

from display import Display
from microphone import TableMic
from speech import SpeechToText

REFRESH_SECONDS = 0.25  # how often the board is re-arranged (fading, dwell times)

display = Display()


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


def refresh_forever():
    while True:
        time.sleep(REFRESH_SECONDS)
        display.tick()


Bridge.provide("knob_turned", lambda steps: display.turn(int(steps)))
Bridge.provide("knob_pressed", display.press)
threading.Thread(target=refresh_forever, daemon=True).start()

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
