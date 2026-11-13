"""Switches the monitor itself on and off, standard library only (runs on the board).

Through the X server's DPMS extension (libXext via ctypes), like `xset dpms force
off`: the board stops sending a picture and the monitor goes to standby, backlight
off. On the 7" Elecrow (2.5 W) that saves most of its power - a black picture
doesn't: an LCD's backlight shines on behind black pixels.

    monitor = Monitor()      # OSError if it can't be done (no X, no DPMS)
    monitor.power(False)     # standby
    monitor.power(True)      # on again (a touch on the screen does that too: X server)

keep_up decides when the monitor sleeps: the X server's own DPMS timeouts are
switched off, so it doesn't go dark in the middle of a conversation for lack of
mouse or touch input.

Not thread-safe: use it from one thread (board_host.py's main thread).
"""

import ctypes

_DPMS_ON, _DPMS_OFF = 0, 3  # DPMSModeOn, DPMSModeOff from <X11/extensions/dpms.h>


class Monitor:
    def __init__(self):
        x11, xext = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXext.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        xext.DPMSQueryExtension.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                            ctypes.POINTER(ctypes.c_int)]
        xext.DPMSCapable.argtypes = [ctypes.c_void_p]
        xext.DPMSEnable.argtypes = [ctypes.c_void_p]
        xext.DPMSSetTimeouts.argtypes = [ctypes.c_void_p] + [ctypes.c_ushort] * 3
        xext.DPMSForceLevel.argtypes = [ctypes.c_void_p, ctypes.c_ushort]

        display = x11.XOpenDisplay(None)  # $DISPLAY
        if not display:
            raise OSError("cannot open the X display")
        unused = ctypes.c_int()
        if not (xext.DPMSQueryExtension(display, ctypes.byref(unused), ctypes.byref(unused))
                and xext.DPMSCapable(display)):
            raise OSError("the X server cannot switch the monitor off (no DPMS)")
        xext.DPMSEnable(display)  # needed to force a level; then its timeouts would
        xext.DPMSSetTimeouts(display, 0, 0, 0)  # apply too: none
        x11.XSync(display, 0)
        self._x11, self._xext, self._display = x11, xext, display

    def power(self, on: bool) -> None:
        self._xext.DPMSForceLevel(self._display, _DPMS_ON if on else _DPMS_OFF)
        self._x11.XSync(self._display, 0)  # done when this returns
