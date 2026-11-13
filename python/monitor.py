"""Switches the monitor itself on and off (runs on the board, standard library only).

Uses the X server's DPMS extension through libXext, like `xset dpms force off`:
the board stops sending a picture and the monitor goes to standby with its
backlight off. A black picture saves little: an LCD's backlight shines on behind
black pixels.

    monitor = Monitor()      # OSError if the X server can't do it
    monitor.power(False)     # standby
    monitor.power(True)      # on again (a touch on the screen does that too)

keep_up decides when the monitor sleeps, so the X server's own DPMS timeouts are
switched off. Not thread-safe: use it from one thread.
"""

import ctypes

_DPMS_ON, _DPMS_OFF = 0, 3  # DPMSModeOn, DPMSModeOff from <X11/extensions/dpms.h>


class Monitor:
    def __init__(self) -> None:
        x11, xext = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXext.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        xext.DPMSQueryExtension.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        xext.DPMSCapable.argtypes = [ctypes.c_void_p]
        xext.DPMSEnable.argtypes = [ctypes.c_void_p]
        xext.DPMSSetTimeouts.argtypes = [ctypes.c_void_p] + [ctypes.c_ushort] * 3
        xext.DPMSForceLevel.argtypes = [ctypes.c_void_p, ctypes.c_ushort]

        display = x11.XOpenDisplay(None)  # $DISPLAY
        if not display:
            raise OSError("cannot open the X display")
        unused = ctypes.c_int()
        if not (
            xext.DPMSQueryExtension(display, ctypes.byref(unused), ctypes.byref(unused))
            and xext.DPMSCapable(display)
        ):
            raise OSError("the X server cannot switch the monitor off (no DPMS)")
        xext.DPMSEnable(display)  # needed to force a level ...
        xext.DPMSSetTimeouts(display, 0, 0, 0)  # ... but then its timeouts apply too: none
        x11.XSync(display, 0)
        self._x11, self._xext, self._display = x11, xext, display

    def power(self, on: bool) -> None:
        self._xext.DPMSForceLevel(self._display, _DPMS_ON if on else _DPMS_OFF)
        self._x11.XSync(self._display, 0)  # done when this returns
