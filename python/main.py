"""Live speech to text, fully offline on the UNO Q, shown on the attached monitor.

Audio from a USB microphone on the board is streamed to the local_asr brick
(Cactus Whistle model on the board's CPU). The Modulino Knob on the sketch side
picks how much of it is shown: the 1, 2, 3, ... most important words, or the
full live transcript (see display.py). Turning sends "knob_turned", pressing
"knob_pressed" over the Bridge.
"""

import asyncio
import sys
import threading
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
# The brick vendors its Python deps (aiohttp, ...) for aarch64 / CPython 3.13
sys.path.insert(0, str(APP_ROOT / "bricks/local_asr/vendor"))

import numpy as np  # noqa: E402
from arduino.app_peripherals.microphone import Microphone  # noqa: E402
from arduino.app_utils import App, Bridge  # noqa: E402
from display import Display  # noqa: E402
from local_asr import LocalASR  # noqa: E402

RATE = 16000
CHUNK_SECONDS = 1.0  # audio per message to the brick (max 1 s); brick queues at most 4
MAX_AHEAD = 1.0  # drop mic backlog that would put us more than this ahead of real time
REFRESH_SECONDS = 1.0  # how often the important words are re-picked

asr = LocalASR()
display = Display()


def wait_until_ready():
    display.message("Loading model ...")
    print("Waiting for speech model ...")
    while True:
        try:
            health = asr.health()
            if health.get("ready"):
                print(f"Model ready: {health['engine']} (loaded in {health['load_seconds']:.1f}s)")
                return
        except Exception:
            pass
        time.sleep(1)


def open_mic():
    try:
        mic = Microphone()  # defaults = what the model wants: 16 kHz, mono, int16
        mic.start()
        print(f"Microphone: {mic.name}")
        return mic
    except Exception as e:
        print(f"No microphone ({e})")
        return None


async def mic_chunks(mic):
    """Microphone -> PCM16 chunks of exactly CHUNK_SECONDS, never faster than real time.

    The mic buffers audio while nobody reads it (e.g. during a restart). Sending that
    backlog in a burst overflows the brick's 4-chunk queue, so stale chunks are dropped.
    """
    frames = mic.stream()
    need = int(RATE * CHUNK_SECONDS)
    buf = np.zeros(0, dtype=np.int16)
    start, sent, dropped = time.monotonic(), 0.0, 0
    while True:
        block = await asyncio.to_thread(next, frames, None)
        if block is None:
            return
        buf = np.concatenate([buf, np.asarray(block, dtype=np.int16).reshape(-1)])
        while len(buf) >= need:
            chunk, buf = buf[:need], buf[need:]
            if sent + CHUNK_SECONDS > time.monotonic() - start + MAX_AHEAD:
                dropped += 1
                continue
            if dropped:
                print(f"(dropped {dropped}s of buffered audio)")
                dropped = 0
            sent += CHUNK_SECONDS
            yield chunk.tobytes()


async def session(chunks):
    """One streaming session: hand captions to the display until the audio ends.

    Events: "text" = newly confirmed words (append), "pending" = tentative tail (replace).
    """
    async for event in asr.stream(chunks):
        new = event.get("text") or ""
        if new.strip():
            print(f"📝 {new.strip()}")
        display.caption(new, event.get("pending") or "")


def refresh_forever():
    while True:
        time.sleep(REFRESH_SECONDS)
        display.tick()


Bridge.provide("knob_turned", lambda steps: display.turn(int(steps)))
Bridge.provide("knob_pressed", display.press)
threading.Thread(target=refresh_forever, daemon=True).start()

wait_until_ready()
mic = open_mic()
display.message("Listening ..." if mic is not None else "No microphone - plug one in and redeploy")


def loop():
    try:
        if mic is not None:
            asyncio.run(session(mic_chunks(mic)))
        else:
            time.sleep(60)
    except Exception as e:
        print(f"Stream error: {e!r} - restarting")
        time.sleep(0.2)


App.run(user_loop=loop)
