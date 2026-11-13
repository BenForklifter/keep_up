"""The microphone the app listens with: the reSpeaker XVF3800 if plugged in,
otherwise the first USB microphone.

The XVF3800 does the hard part of listening at a table in its own DSP: it
steers its 4 microphones towards whoever speaks (beamforming) and takes out
noise, echo and reverb. Its first capture channel carries that processed beam
(firmware default "auto-select beam"); further channels are silence or raw
microphones, depending on the firmware (2- or 6-channel). Only the beam is used.

    mic = TableMic()
    for samples in mic.blocks():   # mono int16 at 16 kHz
        ...
"""

import re
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from arduino.app_peripherals.microphone import Microphone

RATE = 16000
RESPEAKER_USB_ID = "2886:001a"
BEAM_CHANNEL = 0


class TableMic:
    def __init__(self):
        respeaker = _find_respeaker()
        if respeaker:
            card, self._channels = respeaker
            self._mic = Microphone(f"plughw:CARD={card},DEV=0",
                                   sample_rate=RATE, channels=self._channels)
            self.name = f"reSpeaker XVF3800 ({self._channels} channels, using the processed beam)"
        else:
            self._channels = 1
            self._mic = Microphone(sample_rate=RATE, channels=1)  # first USB microphone
            self.name = self._mic.name
        self._mic.start()

    def blocks(self) -> Iterator[np.ndarray]:
        """Mono int16 sample blocks while the microphone delivers audio."""
        for block in self._mic.stream():
            frames = np.asarray(block, dtype=np.int16).reshape(-1, self._channels)
            yield frames[:, BEAM_CHANNEL]


def _find_respeaker() -> tuple[str, int] | None:
    """ALSA card id and capture channel count of a plugged-in XVF3800."""
    for usb_id in Path("/proc/asound").glob("card*/usbid"):
        if usb_id.read_text().strip() == RESPEAKER_USB_ID:
            card = usb_id.parent
            return (card / "id").read_text().strip(), _capture_channels(card / "stream0")
    return None


def _capture_channels(stream_info: Path) -> int:
    _, found, capture = stream_info.read_text().partition("Capture:")
    channels = [int(n) for n in re.findall(r"Channels: (\d+)", capture)] if found else []
    return max(channels, default=1)
