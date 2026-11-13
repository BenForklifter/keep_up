"""The microphone the app listens with: the reSpeaker XVF3800 if plugged in,
otherwise the first USB microphone.

The XVF3800 does the hard part of listening at a table in its own DSP: it
steers its 4 microphones towards whoever speaks (beamforming) and takes out
noise, echo and reverb. Its first capture channel carries that processed beam
(firmware default "auto-select beam"); the second is silence or a raw
microphone. So it is opened in stereo and only the first channel is used - a
mono capture would average both.

    mic = TableMic()
    for samples in mic.blocks():   # mono int16 at 16 kHz
        ...
"""

from collections.abc import Iterator
from pathlib import Path

import numpy as np
from arduino.app_peripherals.microphone import Microphone

RATE = 16000
RESPEAKER_USB_ID = ("2886", "001a")
CHANNELS, BEAM_CHANNEL = 2, 0  # also right for the 6-channel firmware: ALSA hands over the first two


class TableMic:
    def __init__(self):
        card = _find_respeaker()
        if card:
            self._channels = CHANNELS
            self._mic = Microphone(f"plughw:CARD={card},DEV=0", sample_rate=RATE, channels=CHANNELS)
            self.name = f"reSpeaker XVF3800 (card {card}, processed beam on channel {BEAM_CHANNEL})"
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


def _find_respeaker() -> str | None:
    """ALSA card id of a plugged-in XVF3800.

    Looked up in /sys: containers usually don't see /proc/asound.
    """
    for card in Path("/sys/class/sound").glob("card*"):
        usb_device = (card / "device").resolve().parent  # the card's device is a USB interface
        try:
            ids = tuple((usb_device / f).read_text().strip() for f in ("idVendor", "idProduct"))
            if ids == RESPEAKER_USB_ID:
                return (card / "id").read_text().strip()
        except OSError:
            continue  # not a USB sound card
    return None
