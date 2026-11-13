"""Fullscreen text display driver for the attached monitor.

    from screen import Screen

    screen = Screen()        # opens a white fullscreen window
    screen.show("Apfel")     # draws the text as large as possible (black, centered)
    screen.show("Apfel", status="3")  # ... plus a small label in the bottom-left corner
    screen.show("Apfel", info="2.4 W")  # ... and/or one in the bottom-right corner
    screen.show("<span foreground='#999'>alt</span> neu", markup=True)  # Pango markup
    screen.point_to(90)      # a glow at the edge towards the speaker (mic array degrees)
    screen.point_to(None)    # ... gone again
    screen.close()           # closes the window

The text size stays steady while the text changes a little (no jumping with every
new word); it shrinks at once when text no longer fits and grows only when it
could get clearly bigger.

The glow is a soft blue spot on the screen edge, like a blurred blob: strongest
right at the edge, fading out smoothly along it and well into the screen. It
fades in and out; when it moves, the old one fades while the new one appears.

Setting it up, once: tap (or click) the screen and a Settings button shows for a
few seconds. It opens a card with buttons to turn and flip the glow until it
points at whoever speaks - that depends on how the mic array lies on the table.
Changes are saved right away (shared.SETTINGS_FILE); Done, or a minute
untouched, closes the card again.

show() and point_to() are thread-safe and return immediately; GTK runs in its
own background thread.
"""
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, fields

from shared import SETTINGS_FILE, read_json, write_json

os.environ.setdefault("DISPLAY", ":0")
os.environ.setdefault("XAUTHORITY", os.path.expanduser("~/.Xauthority"))

import cairo  # noqa: E402
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

GLOW_ALONG = 0.42  # glow towards the speaker: reach along the edge to either side,
                   # fraction of the shorter screen side ...
GLOW_DEPTH = 0.22  # ... and into the screen (behind the text there, but light by then)
GLOW_COLOR = (0x0F / 255, 0x6C / 255, 0xBD / 255)  # right at the edge: Fluent blue #0f6cbd,
                                                   # the same as the Done button (UI_CSS)
GLOW_STEPS = 16    # gradient stops of its fade (more = smoother)
GLOW_FADE_SECONDS = 0.3
FADE_FRAME_MS = 40  # redraw interval while a glow fades (none otherwise)

TURN_STEP = 30     # degrees per tap on a turn button (one LED of the mic array)
OFFER_SECONDS = 8  # the Settings button goes again after this long ...
SETTINGS_SECONDS = 60  # ... the settings card after this long untouched
OFFER_BOTTOM = 96  # pixels between the Settings button and the bottom edge
SETTINGS_TITLE = "Speaker direction"
SETTINGS_TEXT = "Let someone talk, then turn the blue glow\nuntil it points at them."
UI_CSS = b"""
.ui { font-family: "Segoe UI", "Noto Sans", "DejaVu Sans", sans-serif; }
.ui.card {
    background-color: #ffffff;
    border-radius: 8px;
    padding: 28px 32px 24px 32px;
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.18), 0 0 4px rgba(0, 0, 0, 0.10);
}
.ui .title { font-size: 26px; font-weight: 600; color: #242424; }
.ui .text { font-size: 19px; color: #616161; margin: 8px 0 24px 0; }
.ui button {
    background-image: none;
    background-color: #ffffff;
    border: 1px solid #d1d1d1;
    border-radius: 6px;
    box-shadow: none;
    text-shadow: none;
    outline-color: transparent;
    color: #242424;
    font-size: 19px;
    font-weight: 600;
    padding: 10px 22px;
}
.ui button:hover { background-color: #f5f5f5; border-color: #c7c7c7; }
.ui button:active { background-color: #e0e0e0; border-color: #b3b3b3; }
.ui button.primary { background-color: #0f6cbd; border-color: #0f6cbd; color: #ffffff; }
.ui button.primary:hover { background-color: #115ea3; border-color: #115ea3; }
.ui button.primary:active { background-color: #0f548c; border-color: #0f548c; }
.ui button.offer {
    border-color: #e0e0e0;
    border-radius: 22px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.16);
}
"""


@dataclass
class _Settings:
    turn: int = 180  # degrees added to the mic array's angle ...
    mirrored: bool = False  # ... after counting it the other way round

    def angle(self, direction: float) -> float:
        """Screen angle (0 = top edge, clockwise) towards a mic array direction."""
        return (self.turn + (-direction if self.mirrored else direction)) % 360


@dataclass
class _Glow:
    angle: float  # degrees, 0 = top edge, clockwise
    opacity: float = 0.0
    target: float = 1.0  # opacity it fades towards


class Screen:
    def __init__(self):
        self._text = ""
        self._status = ""
        self._info = ""
        self._markup = False
        self._scale = 0.0
        self._settings = _load_settings()
        self._direction = None  # where the speaker is, in mic array degrees (None: silence) ...
        self._last_direction = None  # ... and where the last one was (shown while setting up)
        self._glows: list[_Glow] = []  # the one shown, and earlier ones still fading out
        self._fading = None  # GLib timer while glows fade
        self._faded_at = 0.0
        self._panel_timer = None  # GLib timer that hides the settings panel
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

    def point_to(self, direction: float | None) -> None:
        """Glow at the screen edge towards whoever speaks; None: nobody (it fades out).

        `direction` is in degrees as the mic array counts them; the settings turn
        and flip that to the screen.
        """
        GLib.idle_add(self._set_direction, direction)

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

        css = Gtk.CssProvider()
        css.load_from_data(UI_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self._window = Gtk.Window()
        self._area = Gtk.DrawingArea()
        self._area.connect("draw", self._draw)
        self._area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._area.connect("button-press-event", self._on_tap)
        overlay = Gtk.Overlay()
        overlay.add(self._area)
        self._add_settings(overlay)
        self._window.add(overlay)
        self._window.fullscreen()
        self._window.show_all()
        self._show_panel(None)  # until the screen is tapped; also hides the mouse pointer

        self._ready.set()
        Gtk.main()
        self._window.destroy()

    def _set_text(self, text, status, markup, info):
        self._text, self._status, self._markup, self._info = text, status, markup, info
        self._area.queue_draw()
        return False  # run once

    # --- the glow --------------------------------------------------------------

    def _set_direction(self, direction):
        self._direction = direction
        if direction is not None:
            self._last_direction = direction
        self._update_glow()
        return False  # run once

    def _update_glow(self):
        """Fade the glow to where the speaker is (while setting up: was, in silence)."""
        direction = self._direction
        if direction is None and self._card.get_visible():
            direction = self._last_direction
        angle = None if direction is None else self._settings.angle(direction)
        for glow in self._glows:  # the same glow stays (or comes back), others fade out
            glow.target = 1.0 if glow.angle == angle else 0.0
        if angle is not None and not any(glow.target for glow in self._glows):
            self._glows.append(_Glow(angle))
        if self._fading is None and any(glow.opacity != glow.target for glow in self._glows):
            self._faded_at = time.monotonic()
            self._fading = GLib.timeout_add(FADE_FRAME_MS, self._fade)

    def _fade(self):
        """One step of fading glows in and out; stops when all are done."""
        now = time.monotonic()
        step, self._faded_at = (now - self._faded_at) / GLOW_FADE_SECONDS, now
        for glow in self._glows:
            if glow.opacity < glow.target:
                glow.opacity = min(glow.opacity + step, glow.target)
            else:
                glow.opacity = max(glow.opacity - step, glow.target)
        self._glows = [glow for glow in self._glows if glow.opacity > 0 or glow.target > 0]
        self._area.queue_draw()
        if all(glow.opacity == glow.target for glow in self._glows):
            self._fading = None
            return False  # no more redraws until the next change
        return True

    # --- settings ---------------------------------------------------------------

    def _add_settings(self, overlay):
        """The Settings button near the bottom, and the card it opens in the
        middle (both hidden at first). Their margins leave room for the shadows."""
        self._offer = _styled(Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin=16), "ui")
        self._offer.set_margin_bottom(OFFER_BOTTOM)
        self._offer.add(_button("Settings", lambda: self._show_panel("settings"), "offer"))

        turns = Gtk.Box(spacing=12)
        for button in (_button("↺  Turn left", lambda: self._turn(-TURN_STEP)),
                       _button("↻  Turn right", lambda: self._turn(TURN_STEP)),
                       _button("⇅  Flip", self._flip)):
            turns.add(button)
        done = _button("Done", lambda: self._show_panel(None), "primary")
        done.set_halign(Gtk.Align.END)
        done.set_margin_top(28)
        self._card = _styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER,
                                     valign=Gtk.Align.CENTER, margin=40), "ui", "card")
        for widget in (_styled(Gtk.Label(label=SETTINGS_TITLE, xalign=0), "title"),
                       _styled(Gtk.Label(label=SETTINGS_TEXT, xalign=0), "text"),
                       turns, done):
            self._card.add(widget)

        overlay.add_overlay(self._offer)
        overlay.add_overlay(self._card)

    def _on_tap(self, _widget, _event):
        if not self._card.get_visible():
            self._show_panel("offer")
        return True

    def _show_panel(self, part):
        """Show None (nothing), "offer" (the Settings button) or "settings" (the
        card); the latter two go again when left untouched."""
        self._offer.set_visible(part == "offer")
        self._card.set_visible(part == "settings")
        self._set_cursor(visible=part is not None)
        if self._panel_timer:
            GLib.source_remove(self._panel_timer)
            self._panel_timer = None
        if part:
            seconds = OFFER_SECONDS if part == "offer" else SETTINGS_SECONDS
            self._panel_timer = GLib.timeout_add_seconds(seconds, self._panel_timed_out)
        self._update_glow()

    def _panel_timed_out(self):
        self._panel_timer = None
        self._show_panel(None)
        return False

    def _turn(self, degrees):
        self._settings.turn = (self._settings.turn + degrees) % 360
        self._settings_changed()

    def _flip(self):
        """Top and bottom swap places."""
        self._settings.mirrored = not self._settings.mirrored
        self._settings.turn = (180 - self._settings.turn) % 360
        self._settings_changed()

    def _settings_changed(self):
        """Save, move the glow, and keep the card open a while longer."""
        write_json(SETTINGS_FILE, asdict(self._settings))
        self._show_panel("settings")

    def _set_cursor(self, visible):
        """The mouse pointer only shows while the settings panel is up."""
        gdk_window = self._window.get_window()
        if gdk_window:
            kind = Gdk.CursorType.LEFT_PTR if visible else Gdk.CursorType.BLANK_CURSOR
            gdk_window.set_cursor(Gdk.Cursor.new_for_display(gdk_window.get_display(), kind))

    # --- drawing ----------------------------------------------------------------

    def _draw(self, widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        self._draw_glows(cr, width, height)

        # The status band at the bottom is always reserved, so text doesn't jump.
        status_height = height * STATUS_HEIGHT
        text_height = height - status_height
        self._draw_text(cr, width / 2, text_height / 2,
                        width * (1 - 2 * MARGIN), text_height - 2 * height * MARGIN)
        self._draw_status(cr, height * MARGIN, height - height * MARGIN, status_height)
        self._draw_info(cr, width - height * MARGIN, height - height * MARGIN, height * INFO_HEIGHT)

    def _draw_glows(self, cr, width, height):
        """Each glow: an ellipse centered on its edge point (half of it off
        screen), its gray fading out from the center like a blur."""
        side = min(width, height)
        along, depth = side * GLOW_ALONG, side * GLOW_DEPTH
        for glow in self._glows:
            x, y, on_side_edge = _edge_point(glow.angle, width, height)
            spot = cairo.RadialGradient(0, 0, 0, 0, 0, 1)
            for i in range(GLOW_STEPS + 1):
                r = i / GLOW_STEPS
                alpha = math.exp(-3 * r * r) * (1 - r * r) * glow.opacity  # Gaussian, 0 at r = 1
                spot.add_color_stop_rgba(r, *GLOW_COLOR, alpha)
            cr.save()
            cr.translate(x, y)
            if on_side_edge:
                cr.scale(depth, along)
            else:
                cr.scale(along, depth)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.set_source(spot)
            cr.fill()
            cr.restore()

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


def _load_settings() -> _Settings:
    saved = read_json(SETTINGS_FILE) or {}
    return _Settings(**{f.name: saved[f.name] for f in fields(_Settings) if f.name in saved})


def _styled(widget, *css_classes):
    for css_class in css_classes:
        widget.get_style_context().add_class(css_class)
    return widget


def _button(label, action, *css_classes):
    button = _styled(Gtk.Button(label=label), *css_classes)
    button.connect("clicked", lambda _: action())
    return button


def _edge_point(angle, width, height):
    """Where a line from the screen center at angle (0 = up, clockwise) meets the
    screen edge: x, y, and whether that is the left or right edge."""
    dx, dy = math.sin(math.radians(angle)), -math.cos(math.radians(angle))
    on_side_edge = abs(dx) * height > abs(dy) * width
    reach = width / 2 / abs(dx) if on_side_edge else height / 2 / abs(dy)
    return width / 2 + dx * reach, height / 2 + dy * reach, on_side_edge


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
