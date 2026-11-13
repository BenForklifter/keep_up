"""Fullscreen text display driver for the attached monitor.

    from screen import Screen

    screen = Screen()        # opens a white fullscreen window
    screen.show("Apfel")     # draws the text as large as possible (black, centered)
    screen.show("Apfel", status="3")  # ... plus a small label in the bottom-left corner
    screen.show("Apfel", info="2.4 W")  # ... and/or one in the bottom-right corner
    screen.show("<span foreground='#999'>alt</span> neu", markup=True)  # Pango markup
    screen.close()           # closes the window

The text size stays steady while the text changes a little (no jumping with every
new word); it shrinks at once when text no longer fits and grows only when it
could get clearly bigger.

show() is thread-safe and returns immediately; GTK runs in its own background thread.
"""
import os
import threading

os.environ.setdefault("DISPLAY", ":0")
os.environ.setdefault("XAUTHORITY", os.path.expanduser("~/.Xauthority"))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

FONT = "DejaVu Sans Bold"
MARGIN = 0.05      # free border on each side, fraction of screen size
STATUS_HEIGHT = 0.06  # bottom-left status label, fraction of screen height
INFO_HEIGHT = 0.035  # bottom-right info label (smaller: it is longer)
STATUS_GRAY = 0.55
GROW_AT = 1.3      # grow the text only when it could get this much bigger
_REF_SIZE = 100    # font size used for measuring before scaling


class Screen:
    def __init__(self):
        self._text = ""
        self._status = ""
        self._info = ""
        self._markup = False
        self._scale = 0.0
        self._ready = threading.Event()
        self._error = None
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait()
        if self._error:
            raise RuntimeError(self._error)

    def show(self, text: str, status: str = "", markup: bool = False, info: str = "") -> None:
        """Display text (and small status / info labels), replacing whatever is shown.

        With markup=True, text is Pango markup (colors, <b>, ...).
        """
        GLib.idle_add(self._set_text, str(text), str(status), markup, str(info))

    def close(self) -> None:
        GLib.idle_add(Gtk.main_quit)

    # --- GTK thread ---------------------------------------------------------

    def _run(self):
        ok, _ = Gtk.init_check(None)
        if not ok:
            self._error = (f"Cannot open display {os.environ['DISPLAY']} - "
                           "is user 'arduino' logged in on the monitor?")
            self._ready.set()
            return

        self._window = Gtk.Window()
        self._area = Gtk.DrawingArea()
        self._area.connect("draw", self._draw)
        self._window.add(self._area)
        self._window.connect("realize", self._hide_cursor)
        self._window.fullscreen()
        self._window.show_all()

        self._ready.set()
        Gtk.main()
        self._window.destroy()

    def _set_text(self, text, status, markup, info):
        self._text, self._status, self._markup, self._info = text, status, markup, info
        self._area.queue_draw()
        return False  # run once

    @staticmethod
    def _hide_cursor(window):
        gdk_window = window.get_window()
        gdk_window.set_cursor(
            Gdk.Cursor.new_for_display(gdk_window.get_display(), Gdk.CursorType.BLANK_CURSOR))

    def _draw(self, widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        cr.set_source_rgb(1, 1, 1)
        cr.paint()

        # The status band at the bottom is always reserved, so text doesn't jump.
        status_height = height * STATUS_HEIGHT
        text_height = height - status_height
        self._draw_text(cr, width / 2, text_height / 2,
                        width * (1 - 2 * MARGIN), text_height - 2 * height * MARGIN)
        self._draw_status(cr, height * MARGIN, height - height * MARGIN, status_height)
        self._draw_info(cr, width - height * MARGIN, height - height * MARGIN, height * INFO_HEIGHT)

    def _draw_text(self, cr, center_x, center_y, max_width, max_height):
        """The text, as large as fits into the box, centered on (center_x, center_y)."""
        layout = _layout(cr, self._text, self._markup)
        ink, _ = layout.get_pixel_extents()
        if ink.width == 0 or ink.height == 0:
            return
        fit = min(max_width / ink.width, max_height / ink.height)
        if fit < self._scale or fit > self._scale * GROW_AT:
            self._scale = fit
        cr.save()
        cr.translate(center_x, center_y)
        cr.scale(self._scale, self._scale)
        cr.translate(-(ink.x + ink.width / 2), -(ink.y + ink.height / 2))
        cr.set_source_rgb(0, 0, 0)
        PangoCairo.show_layout(cr, layout)
        cr.restore()

    def _draw_status(self, cr, left, bottom, size):
        """Small gray label whose bottom-left corner is at (left, bottom)."""
        layout = _layout(cr, self._status, markup=False)
        ink, _ = layout.get_pixel_extents()
        if ink.height == 0:
            return
        scale = size / ink.height
        cr.save()
        cr.translate(left, bottom)
        cr.scale(scale, scale)
        cr.translate(-ink.x, -(ink.y + ink.height))
        cr.set_source_rgb(STATUS_GRAY, STATUS_GRAY, STATUS_GRAY)
        PangoCairo.show_layout(cr, layout)
        cr.restore()

    def _draw_info(self, cr, right, bottom, size):
        """Small gray label whose bottom-right corner is at (right, bottom)."""
        layout = _layout(cr, self._info, markup=False)
        ink, _ = layout.get_pixel_extents()
        if ink.height == 0:
            return
        scale = size / ink.height
        cr.save()
        cr.translate(right, bottom)
        cr.scale(scale, scale)
        cr.translate(-(ink.x + ink.width), -(ink.y + ink.height))
        cr.set_source_rgb(STATUS_GRAY, STATUS_GRAY, STATUS_GRAY)
        PangoCairo.show_layout(cr, layout)
        cr.restore()


def _layout(cr, text, markup):
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription(FONT)
    font.set_size(_REF_SIZE * Pango.SCALE)
    layout.set_font_description(font)
    layout.set_alignment(Pango.Alignment.CENTER)
    if markup:
        layout.set_markup(text, -1)
    else:
        layout.set_text(text, -1)
    return layout
