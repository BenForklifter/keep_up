"""Fullscreen text display driver for the attached monitor.

    from screen import Screen

    screen = Screen()        # opens a white fullscreen window
    screen.show("Apfel")     # draws the text as large as possible (black, centered)
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
_REF_SIZE = 100    # font size used for measuring before scaling


class Screen:
    def __init__(self):
        self._text = ""
        self._ready = threading.Event()
        self._error = None
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait()
        if self._error:
            raise RuntimeError(self._error)

    def show(self, text: str) -> None:
        """Display text, replacing whatever is currently shown."""
        GLib.idle_add(self._set_text, str(text))

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

    def _set_text(self, text):
        self._text = text
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

        layout = PangoCairo.create_layout(cr)
        font = Pango.FontDescription(FONT)
        font.set_size(_REF_SIZE * Pango.SCALE)
        layout.set_font_description(font)
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_text(self._text, -1)

        ink, _ = layout.get_pixel_extents()
        if ink.width == 0 or ink.height == 0:
            return

        scale = min(width * (1 - 2 * MARGIN) / ink.width,
                    height * (1 - 2 * MARGIN) / ink.height)
        cr.translate(width / 2, height / 2)
        cr.scale(scale, scale)
        cr.translate(-(ink.x + ink.width / 2), -(ink.y + ink.height / 2))
        cr.set_source_rgb(0, 0, 0)
        PangoCairo.show_layout(cr, layout)
