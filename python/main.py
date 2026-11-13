"""Live speech to text, fully offline on the UNO Q, shown on the attached monitor.

Audio from a USB microphone on the board is streamed to the local_asr brick
(Cactus Whistle model on the board's CPU). Captions arrive while you talk and
are written to .screen/text, which python/screen_host.py (running on the board
itself) puts on the monitor.
"""

import asyncio
import os
import sys
import textwrap
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
# The brick vendors its Python deps (aiohttp, ...) for aarch64 / CPython 3.13
sys.path.insert(0, str(APP_ROOT / "bricks/local_asr/vendor"))

import numpy as np  # noqa: E402
from arduino.app_peripherals.microphone import Microphone  # noqa: E402
from arduino.app_utils import App  # noqa: E402
from local_asr import LocalASR  # noqa: E402

RATE = 16000
CHUNK_SECONDS = 1.0  # audio per message to the brick (max 1 s); brick queues at most 4
MAX_AHEAD = 1.0  # drop mic backlog that would put us more than this ahead of real time
SCREEN_FILE = APP_ROOT / ".screen/text"
LINE_CHARS = 24  # screen: characters per line ...
MAX_LINES = 3  # ... and number of lines (newest text wins)

asr = LocalASR()


def show(text: str):
    """Hand text to screen_host.py (atomic write so it never reads half a file)."""
    SCREEN_FILE.parent.mkdir(exist_ok=True)
    tmp = SCREEN_FILE.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, SCREEN_FILE)


def join(a: str, b: str) -> str:
    """Caption pieces come without spaces at the edges ("not" + "the")."""
    a, b = a.rstrip(), b.strip()
    return f"{a} {b}" if a and b else a or b


def screen_text(transcript: str) -> str:
    lines = textwrap.wrap(transcript, LINE_CHARS)
    return "\n".join(lines[-MAX_LINES:])


def wait_until_ready():
    show("Loading model ...")
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


confirmed = ""  # kept across stream restarts so the screen doesn't jump back


async def session(chunks):
    """One streaming session: print and display captions until the audio ends.

    Events: "text" = newly confirmed words (append), "pending" = tentative tail (replace).
    """
    global confirmed
    async for event in asr.stream(chunks):
        new = event.get("text") or ""
        if new.strip():
            confirmed = join(confirmed, new)[-500:]
            print(f"📝 {new.strip()}")
        show(screen_text(join(confirmed, event.get("pending") or "")))


wait_until_ready()
mic = open_mic()
show("Listening ..." if mic is not None else "No microphone - plug one in and redeploy")


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
