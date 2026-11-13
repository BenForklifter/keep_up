"""The microphone the app listens with: the reSpeaker XVF3800 if plugged in,
otherwise the first USB microphone.

The XVF3800 does the hard part of listening at a table in its own DSP: it steers
its four microphones towards whoever speaks (beamforming) and removes noise, echo
and reverb. Its first capture channel carries that processed beam; the second is
silence or a raw microphone. So it is opened in stereo and only the first channel
is used, where a mono capture would average both.

    mic = TableMic()
    for samples in mic.blocks():   # mono int16 at 16 kHz
        ...

Test audio (tools/eval): a mono 16 kHz 16-bit WAV dropped into .board/test/ is
played instead of the microphone, at real-time pace. Silence before it lets the
recognizer finish what the room said, silence after it lets the last line
complete. The log marks "test: start <name>" (the silence begins), "test: audio
<name>" (the WAV begins) and "test: end <name>".
"""

import time
import wave
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from arduino.app_peripherals.microphone import Microphone

from shared import BOARD_DIR

RATE = 16000
RESPEAKER_USB_ID = ("2886", "001a")
CHANNELS, BEAM_CHANNEL = 2, 0  # also right for the 6-channel firmware: ALSA hands over two

TEST_DIR = BOARD_DIR / "test"
TEST_CHECK_SECONDS = 0.25
TEST_LEAD_SECONDS = 10.0  # silence before test audio: room lines complete, backlog drains
TEST_TAIL_SECONDS = 8.0  # silence after test audio: the last line completes


class TableMic:
    def __init__(self) -> None:
        card = _find_respeaker()
        if card:
            self._channels = CHANNELS
            self._mic = Microphone(
                f"plughw:CARD={card},DEV=0", sample_rate=RATE, channels=CHANNELS
            )
            self.name = f"reSpeaker XVF3800 (card {card}, beam on channel {BEAM_CHANNEL})"
        else:
            self._channels = 1
            self._mic = Microphone(sample_rate=RATE, channels=1)  # first USB microphone
            self.name = self._mic.name
        self._mic.start()

    def close(self) -> None:
        """Stop listening, which ends blocks(). Any thread, also more than once."""
        self._mic.stop()

    def blocks(self) -> Iterator[np.ndarray]:
        """Mono int16 sample blocks while the microphone delivers audio."""
        test, test_name, test_lead, next_check = None, "", 0, 0.0
        for block in self._mic.stream():
            frames = np.asarray(block, dtype=np.int16).reshape(-1, self._channels)
            samples = frames[:, BEAM_CHANNEL]
            if test is None and time.monotonic() >= next_check:
                next_check = time.monotonic() + TEST_CHECK_SECONDS
                test, test_name = _take_test_audio()
                test_lead = int(TEST_LEAD_SECONDS * RATE) if test is not None else 0
            if test is not None:  # replaces the microphone: same block size and pace
                n = len(samples)
                if test_lead > 0:
                    test_lead -= n
                    if test_lead <= 0:
                        print(f"test: audio {test_name}", flush=True)
                samples, test = np.pad(test[:n], (0, max(0, n - len(test)))), test[n:]
                if not len(test):
                    test = None
                    print(f"test: end {test_name}", flush=True)
            yield samples


def test_audio_waiting() -> bool:
    """Whether a test WAV waits in TEST_DIR (it wakes the app from sleep)."""
    return TEST_DIR.is_dir() and any(TEST_DIR.glob("*.wav"))


def _take_test_audio() -> tuple[np.ndarray | None, str]:
    """Samples of a WAV waiting in TEST_DIR, with silence around it; the file is removed."""
    waiting = sorted(TEST_DIR.glob("*.wav")) if TEST_DIR.is_dir() else []
    if not waiting:
        return None, ""
    path = waiting[0]
    try:
        with wave.open(str(path)) as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
                raise ValueError("test audio must be mono 16 kHz 16-bit")
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    except Exception as e:
        print(f"test: skipped {path.name} ({e!r})", flush=True)
        path.unlink(missing_ok=True)
        return None, ""
    path.unlink()
    print(f"test: start {path.stem} ({len(audio) / RATE:.1f} s)", flush=True)
    lead = np.zeros(int(TEST_LEAD_SECONDS * RATE), np.int16)
    tail = np.zeros(int(TEST_TAIL_SECONDS * RATE), np.int16)
    return np.concatenate([lead, audio, tail]), path.stem


def _find_respeaker() -> str | None:
    """ALSA card id of a plugged-in XVF3800, looked up in /sys (containers usually
    don't see /proc/asound)."""
    for card in Path("/sys/class/sound").glob("card*"):
        usb_device = (card / "device").resolve().parent  # the card's device is a USB interface
        try:
            ids = tuple((usb_device / f).read_text().strip() for f in ("idVendor", "idProduct"))
            if ids == RESPEAKER_USB_ID:
                return (card / "id").read_text().strip()
        except OSError:
            continue  # not a USB sound card
    return None
