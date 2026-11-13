"""USB control of the reSpeaker XVF3800 4-mic array, standard library only.

The array's DSP (beamforming, noise suppression, direction of arrival, LED ring,
mute button) is read and configured with vendor control requests on its USB
device. Protocol and parameter ids follow Seeed's python_control/xvf_host.py
(github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY); talking to the kernel's
usbfs directly saves the pyusb/libusb dependency.

    with XVF3800.open() as array:
        angle, speech = array.read("DOA_VALUE")
        array.write("LED_EFFECT", [LED_EFFECT_DOA])

Needs write access to the USB device node: `./deploy.sh setup` installs a udev rule.
"""

import ctypes
import fcntl
import os
import struct
import time
from pathlib import Path

VENDOR_ID, PRODUCT_ID = "2886", "001a"

# name: (resource id, command id, number of values, struct format of one value)
PARAMETERS = {
    "VERSION": (48, 0, 3, "B"),  # major, minor, patch
    "DOA_VALUE": (20, 18, 2, "H"),  # direction 0..359 degrees, speech detected 0/1
    "GPO_READ_VALUES": (20, 0, 5, "B"),  # outputs X0D11, X0D30 (1 = muted), X0D31, X0D33, X0D39
    "LED_EFFECT": (20, 12, 1, "B"),  # see LED_EFFECT_*
    "LED_DOA_COLOR": (20, 17, 2, "I"),  # 0xRRGGBB of the ring, of the LED towards the speaker
}
LED_EFFECT_OFF, LED_EFFECT_DOA = 0, 4
MUTE_OUTPUT = 1  # index of X0D30 in GPO_READ_VALUES

_STATUS_OK, _STATUS_RETRY = 0, 64
_MAX_RETRIES = 100
_TIMEOUT_MS = 1000


class _CtrlTransfer(ctypes.Structure):
    """struct usbdevfs_ctrltransfer from <linux/usbdevice_fs.h>."""

    _fields_ = [
        ("request_type", ctypes.c_uint8),
        ("request", ctypes.c_uint8),
        ("value", ctypes.c_uint16),
        ("index", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("timeout_ms", ctypes.c_uint32),
        ("data", ctypes.c_void_p),
    ]


# _IOWR('U', 0, struct usbdevfs_ctrltransfer)
_USBDEVFS_CONTROL = (3 << 30) | (ctypes.sizeof(_CtrlTransfer) << 16) | (ord("U") << 8)
_VENDOR_IN, _VENDOR_OUT = 0xC0, 0x40  # vendor request to the device, device->host / host->device


class XVF3800:
    def __init__(self, fd: int):
        self._fd = fd

    @classmethod
    def open(cls) -> "XVF3800":
        """Open the first XVF3800 on USB.

        Raises FileNotFoundError if none is plugged in, PermissionError without the udev rule.
        """
        return cls(os.open(_device_node(), os.O_RDWR))

    def read(self, name: str) -> tuple:
        resource, command, count, fmt = PARAMETERS[name]
        size = 1 + count * struct.calcsize(fmt)  # first byte: status
        for _ in range(_MAX_RETRIES):
            reply = self._transfer(_VENDOR_IN, 0x80 | command, resource, bytes(size))
            if reply[0] == _STATUS_OK:
                return struct.unpack(f"<{count}{fmt}", reply[1:])
            if reply[0] != _STATUS_RETRY:
                raise OSError(f"XVF3800 {name}: status {reply[0]}")
            time.sleep(0.01)
        raise TimeoutError(f"XVF3800 {name}: no answer")

    def write(self, name: str, values: list[int]) -> None:
        resource, command, count, fmt = PARAMETERS[name]
        self._transfer(_VENDOR_OUT, command, resource, struct.pack(f"<{count}{fmt}", *values))

    def close(self) -> None:
        os.close(self._fd)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _transfer(self, request_type: int, value: int, index: int, data: bytes) -> bytes:
        buffer = ctypes.create_string_buffer(data, len(data))
        transfer = _CtrlTransfer(request_type, 0, value, index, len(data), _TIMEOUT_MS,
                                 ctypes.addressof(buffer))
        fcntl.ioctl(self._fd, _USBDEVFS_CONTROL, transfer)  # fills buffer for reads
        return buffer.raw


def _device_node() -> str:
    for device in Path("/sys/bus/usb/devices").iterdir():
        try:
            ids = ((device / "idVendor").read_text().strip(),
                   (device / "idProduct").read_text().strip())
            if ids == (VENDOR_ID, PRODUCT_ID):
                bus, number = (int((device / f).read_text()) for f in ("busnum", "devnum"))
                return f"/dev/bus/usb/{bus:03d}/{number:03d}"
        except (FileNotFoundError, ValueError):
            continue  # interfaces and hubs without these files
    raise FileNotFoundError("No reSpeaker XVF3800 on USB")
