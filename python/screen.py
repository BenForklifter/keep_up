"""Fullscreen text display driver for the attached monitor.

    from screen import Screen

    screen = Screen()        # opens a white fullscreen window
    screen.show("Apfel")     # draws the text as large as possible (black, centered)
    screen.show("Apfel", status="3")  # ... plus a small label in the bottom-left corner
    screen.close()           # closes the window

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
STATUS_GRAY = 0.55
_REF_SIZE = 100    # font size used for measuring before scaling


class Screen:
    def __init__(self):
        self._text = ""
        self._status = ""
        self._ready = threading.Event()
        self._error = None
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait()
        if self._error:
            raise RuntimeError(self._error)

    def show(self, text: str, status: str = "") -> None:
        """Display text (and a small status label), replacing whatever is shown."""
        GLib.idle_add(self._set_text, str(text), str(status))

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

    def _set_text(self, text, status):
        self._text, self._status = text, status
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
        self._draw_text(cr, self._text, width / 2, text_height / 2,
                        width * (1 - 2 * MARGIN), text_height - 2 * height * MARGIN)
        self._draw_status(cr, height * MARGIN, height - height * MARGIN, status_height)

    def _draw_text(self, cr, text, center_x, center_y, max_width, max_height):
        """Text as large as fits into the box, centered on (center_x, center_y)."""
        layout = _layout(cr, text)
        ink, _ = layout.get_pixel_extents()
        if ink.width == 0 or ink.height == 0:
            return
        scale = min(max_width / ink.width, max_height / ink.height)
        cr.save()
        cr.translate(center_x, center_y)
        cr.scale(scale, scale)
        cr.translate(-(ink.x + ink.width / 2), -(ink.y + ink.height / 2))
        cr.set_source_rgb(0, 0, 0)
        PangoCairo.show_layout(cr, layout)
        cr.restore()

    def _draw_status(self, cr, left, bottom, size):
        """Small gray label whose bottom-left corner is at (left, bottom)."""
        layout = _layout(cr, self._status)
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


def _layout(cr, text):
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription(FONT)
    font.set_size(_REF_SIZE * Pango.SCALE)
    layout.set_font_description(font)
    layout.set_alignment(Pango.Alignment.CENTER)
    layout.set_text(text, -1)
    return layout
