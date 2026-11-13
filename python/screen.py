"""Fullscreen text display driver for the attached monitor.

    from screen import Screen

    screen = Screen()        # opens a white fullscreen window
    screen.show("Apfel")     # draws the text as large as possible (black, centered)
    screen.show("Apfel", status="3")  # ... plus a small label in the bottom-left corner
    screen.show("Apfel", info="2.4 W")  # ... and/or one in the bottom-right corner
    screen.show("<span foreground='#999'>alt</span> neu", markup=True)  # Pango markup
    screen.point_to(90)      # a glow at the edge towards the speaker (mic array degrees)
    screen.point_to(None)    # ... gone again
    screen.addressed(True)   # shows that someone is talking to the person at the screen
    screen.addressed(False)  # ... gone again
    screen.close()           # closes the window

The text size stays steady while the text changes a little (no jumping with every
new word); it shrinks at once when text no longer fits and grows only when it
could get clearly bigger.

The glow is a soft blue spot on the screen edge, like a blurred blob: strongest
right at the edge, fading out smoothly along it and well into the screen. It
fades in and out; when it moves, the old one fades while the new one appears.

Someone talking to the person at the screen (they said one of the person's names)
is hard to miss: an orange frame around the screen with "Someone is talking to
you" at the top, and the glow towards the speaker turns orange too.

Setting it up, once: tap (or click) the screen and a Settings button shows for a
few seconds. It opens a card with
- a picture of the mic array as seen from in front of the screen - a disc with
  its USB cable, and the LED towards whoever speaks lit like on the array - and
  buttons to turn and flip it until the cable runs as on the table; then the
  glow points at whoever speaks;
- the names the person at the screen is called by ("Mary, Grandma"), typed on an
  on-screen keyboard (or a real one) in a card of its own.
Changes are saved right away (shared.SETTINGS_FILE); Done, or a minute
untouched, closes the card again.

show(), point_to() and addressed() are thread-safe and return immediately; GTK
runs in its own background thread.
"""
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field, fields

from shared import SETTINGS_FILE, clean_names, read_json, speaker_color, write_json

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

ALERT_COLOR = (0xDA / 255, 0x3B / 255, 0x01 / 255)  # someone talks to the person:
                                                    # Fluent dark orange #da3b01
ALERT_FRAME = 0.025  # its frame around the screen, fraction of the shorter side
ALERT_LABEL = 0.05  # its label at the top: text height, fraction of screen height
ALERT_TEXT = "Someone is talking to you"

FADE_SECONDS = 0.3  # glows and the alert fade in and out this fast
FADE_FRAME_MS = 40  # redraw interval while something fades (none otherwise)

TURN_STEP = 30     # degrees per tap on a turn button (one LED of the mic array)
OFFER_SECONDS = 8  # the Settings button goes again after this long ...
SETTINGS_SECONDS = 60  # ... a settings card after this long untouched
OFFER_BOTTOM = 96  # pixels between the Settings button and the bottom edge
DIRECTION_TITLE = "Speaker direction"
DIRECTION_TEXT = ("Turn the picture until the cable runs\n"
                  "as on the table, seen from in front of\n"
                  "the screen. Then let someone talk: the\n"
                  "glow should point at them, else Flip.")

# The picture of the mic array in the settings, seen from above (the screen's viewer
# at the bottom): a dark disc with its ring of 12 LEDs and the USB cable.
CABLE_DIRECTION = 0  # where the cable leaves the array, in mic array degrees: talk
                     # from the cable's side and read "direction" in .board/mic.json
PREVIEW_SIZE = 168  # pixels
PREVIEW_BODY = (0x24 / 255,) * 3  # #242424
PREVIEW_LED = (0x75 / 255,) * 3  # #757575, LEDs that are off
PREVIEW_CABLE = (0x61 / 255,) * 3  # #616161
LEDS = 12
NAMES_TITLE = "Name"
NAMES_TEXT = ("What is the person at this screen called? Several names\n"
              "with commas between them, e.g. \"Mary, Grandma\". When someone\n"
              "says one, the screen shows they are talking to them.")
NAMES_UNSET = "Not set"
KEY_ROWS = ("QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM,-")  # on-screen keyboard (+ Backspace)
BACKSPACE = "⌫"
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
.ui button.key { min-width: 52px; padding: 10px 0; }
.ui .section { margin-top: 32px; }
.ui .value { font-size: 19px; color: #242424; }
.ui entry {
    background-image: none;
    background-color: #ffffff;
    border: 1px solid #d1d1d1;
    border-bottom: 2px solid #616161;
    border-radius: 6px;
    box-shadow: none;
    color: #242424;
    font-size: 22px;
    padding: 8px 12px;
    margin-bottom: 20px;
}
.ui entry:focus { border-bottom-color: #0f6cbd; }
"""


@dataclass
class _Settings:
    turn: int = 180  # degrees added to the mic array's angle ...
    mirrored: bool = False  # ... after counting it the other way round
    names: list[str] = field(default_factory=list)  # what the person at the screen is called

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
        self._alert = 0.0  # opacity of "someone is talking to you" ...
        self._alert_target = 0.0  # ... and the one it fades towards
        self._fading = None  # GLib timer while glows or the alert fade
        self._faded_at = 0.0
        self._panel = None  # what of the settings shows: None, "offer", "settings", "names"
        self._panel_timer = None  # GLib timer that hides it
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

    def addressed(self, on: bool) -> None:
        """Show that someone is talking to the person at the screen (False: stop)."""
        GLib.idle_add(self._set_addressed, bool(on))

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
        if self._panel == "settings":
            self._preview.queue_draw()  # its LED towards the speaker
        return False  # run once

    def _update_glow(self):
        """Fade the glow to where the speaker is (while setting up: was, in silence)."""
        direction = self._direction
        if direction is None and self._panel == "settings":
            direction = self._last_direction
        angle = None if direction is None else self._settings.angle(direction)
        for glow in self._glows:  # the same glow stays (or comes back), others fade out
            glow.target = 1.0 if glow.angle == angle else 0.0
        if angle is not None and not any(glow.target for glow in self._glows):
            self._glows.append(_Glow(angle))
        self._start_fading()

    def _set_addressed(self, on):
        self._alert_target = 1.0 if on else 0.0
        self._start_fading()
        return False  # run once

    def _start_fading(self):
        if self._fading is None and self._still_fading():
            self._faded_at = time.monotonic()
            self._fading = GLib.timeout_add(FADE_FRAME_MS, self._fade)

    def _still_fading(self):
        return (self._alert != self._alert_target
                or any(glow.opacity != glow.target for glow in self._glows))

    def _fade(self):
        """One step of fading glows and the alert in and out; stops when all are done."""
        now = time.monotonic()
        step, self._faded_at = (now - self._faded_at) / FADE_SECONDS, now
        for glow in self._glows:
            glow.opacity = _towards(glow.opacity, glow.target, step)
        self._glows = [glow for glow in self._glows if glow.opacity > 0 or glow.target > 0]
        self._alert = _towards(self._alert, self._alert_target, step)
        self._area.queue_draw()
        if self._still_fading():
            return True
        self._fading = None
        return False  # no more redraws until the next change

    # --- settings ---------------------------------------------------------------

    def _add_settings(self, overlay):
        """The Settings button near the bottom, the card it opens in the middle,
        and the card for typing the names (all hidden at first). Their margins
        leave room for the shadows."""
        self._offer = _styled(Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin=16), "ui")
        self._offer.set_margin_bottom(OFFER_BOTTOM)
        self._offer.add(_button("Settings", lambda: self._show_panel("settings"), "offer"))

        self._preview = Gtk.DrawingArea(valign=Gtk.Align.CENTER)
        self._preview.set_size_request(PREVIEW_SIZE, PREVIEW_SIZE)
        self._preview.connect("draw", self._draw_preview)
        turns = Gtk.Box(spacing=12)
        for button in (_button("↺  Turn left", lambda: self._turn(-TURN_STEP)),
                       _button("↻  Turn right", lambda: self._turn(TURN_STEP)),
                       _button("⇅  Flip", self._flip)):
            turns.add(button)
        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        controls.add(_styled(Gtk.Label(label=DIRECTION_TEXT, xalign=0), "text"))
        controls.add(turns)
        direction = Gtk.Box(spacing=28, margin_top=8)
        direction.add(self._preview)
        direction.add(controls)
        names = Gtk.Box(spacing=12)
        self._names_label = _styled(Gtk.Label(xalign=0, hexpand=True, max_width_chars=26,
                                              ellipsize=Pango.EllipsizeMode.END), "value")
        names.add(self._names_label)
        names.add(_button("✎  Change", self._edit_names))
        self._show_names()
        done = _button("Done", lambda: self._show_panel(None), "primary")
        done.set_halign(Gtk.Align.END)
        done.set_margin_top(28)
        self._card = _card(_styled(Gtk.Label(label=DIRECTION_TITLE, xalign=0), "title"),
                           direction,
                           _styled(Gtk.Label(label=NAMES_TITLE, xalign=0), "title", "section"),
                           names, done)

        self._entry = Gtk.Entry()
        self._entry.connect("changed", lambda _: self._keep_panel())  # also a real keyboard
        self._entry.connect("activate", lambda _: self._save_names())  # Enter
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.END, margin_top=28)
        buttons.add(_button("Cancel", lambda: self._show_panel("settings")))
        buttons.add(_button("Save", self._save_names, "primary"))
        self._names_card = _card(_styled(Gtk.Label(label=NAMES_TITLE, xalign=0), "title"),
                                 _styled(Gtk.Label(label=NAMES_TEXT, xalign=0), "text"),
                                 self._entry, self._keyboard(), buttons)

        for widget in (self._offer, self._card, self._names_card):
            overlay.add_overlay(widget)

    def _keyboard(self):
        """Letter keys, comma, hyphen, Backspace and a wide space bar. They don't
        take the focus, so the text cursor stays in the entry."""
        keyboard = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        rows = [[_key(c, lambda c=c: self._type(c)) for c in row] for row in KEY_ROWS]
        rows[-1].append(_key(BACKSPACE, lambda: self._entry.emit("backspace")))
        space = _key("Space", lambda: self._type(" "))
        space.set_size_request(360, -1)
        rows.append([space])
        for keys in rows:
            row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
            for key in keys:
                row.add(key)
            keyboard.add(row)
        return keyboard

    def _type(self, char):
        """An on-screen key: names start with a capital letter, a comma brings its space."""
        before = self._entry.get_text()[:self._entry.get_position()]
        if char == ",":
            char = ", "
        elif not before or before[-1] in " ,-":
            char = char.upper()
        else:
            char = char.lower()
        self._entry.emit("insert-at-cursor", char)

    def _edit_names(self):
        self._entry.set_text(", ".join(self._settings.names))
        self._show_panel("names")
        self._entry.grab_focus_without_selecting()
        self._entry.set_position(-1)  # at the end

    def _save_names(self):
        self._settings.names = clean_names(self._entry.get_text().split(","))
        self._show_names()
        self._settings_changed()

    def _show_names(self):
        self._names_label.set_text(", ".join(self._settings.names) or NAMES_UNSET)

    def _on_tap(self, _widget, _event):
        if self._panel in (None, "offer"):
            self._show_panel("offer")
        return True

    def _show_panel(self, part):
        """Show None (nothing), "offer" (the Settings button), "settings" (the
        card) or "names" (the card for typing them); all go again when left untouched."""
        self._panel = part
        self._offer.set_visible(part == "offer")
        self._card.set_visible(part == "settings")
        self._names_card.set_visible(part == "names")
        self._set_cursor(visible=part is not None)
        self._keep_panel()
        self._update_glow()

    def _keep_panel(self):
        """(Re)start the time after which the panel shown goes again."""
        if self._panel_timer:
            GLib.source_remove(self._panel_timer)
            self._panel_timer = None
        if self._panel:
            seconds = OFFER_SECONDS if self._panel == "offer" else SETTINGS_SECONDS
            self._panel_timer = GLib.timeout_add_seconds(seconds, self._panel_timed_out)

    def _panel_timed_out(self):
        self._panel_timer = None
        self._show_panel(None)  # names typed but not saved are dropped
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
        """Save, move the glow and the picture, and keep the card open a while longer."""
        write_json(SETTINGS_FILE, asdict(self._settings))
        self._preview.queue_draw()
        self._show_panel("settings")

    def _set_cursor(self, visible):
        """The mouse pointer only shows while the settings panel is up."""
        gdk_window = self._window.get_window()
        if gdk_window:
            kind = Gdk.CursorType.LEFT_PTR if visible else Gdk.CursorType.BLANK_CURSOR
            gdk_window.set_cursor(Gdk.Cursor.new_for_display(gdk_window.get_display(), kind))

    def _draw_preview(self, widget, cr):
        """The mic array seen from above, turned and flipped like the settings turn
        and flip its directions to the screen: the cable runs where it runs on the
        table, and the LED towards the speaker (the last one, in silence) is lit in
        their color, like on the array."""
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        size = min(width, height)
        radius = size * 0.3
        cr.translate(width / 2, height / 2)

        cable_x, cable_y = _unit(self._settings.angle(CABLE_DIRECTION))
        cr.set_source_rgb(*PREVIEW_CABLE)
        for thickness, start, end in ((0.06, 0, size / 2),  # the cable to the edge ...
                                      (0.11, radius * 0.8, radius + size * 0.07)):  # ... its plug
            cr.set_line_width(size * thickness)
            cr.move_to(cable_x * start, cable_y * start)
            cr.line_to(cable_x * end, cable_y * end)
            cr.stroke()

        cr.set_source_rgb(*PREVIEW_BODY)
        cr.arc(0, 0, radius, 0, 2 * math.pi)
        cr.fill()

        lit = self._direction if self._direction is not None else self._last_direction
        for led in range(LEDS):
            direction = led * 360 // LEDS
            x, y = _unit(self._settings.angle(direction))
            on = lit is not None and round(lit * LEDS / 360) % LEDS == led
            cr.set_source_rgb(*(_rgb(speaker_color(direction)) if on else PREVIEW_LED))
            cr.arc(x * radius * 0.75, y * radius * 0.75, size * (0.035 if on else 0.022), 0, 2 * math.pi)
            cr.fill()

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
        self._draw_alert(cr, width, height)

    def _draw_glows(self, cr, width, height):
        """Each glow: an ellipse centered on its edge point (half of it off
        screen), its gray fading out from the center like a blur. Orange while
        someone is talking to the person: it points at who it is."""
        side = min(width, height)
        along, depth = side * GLOW_ALONG, side * GLOW_DEPTH
        color = [blue + (orange - blue) * self._alert for blue, orange in zip(GLOW_COLOR, ALERT_COLOR)]
        for glow in self._glows:
            x, y, on_side_edge = _edge_point(glow.angle, width, height)
            spot = cairo.RadialGradient(0, 0, 0, 0, 0, 1)
            for i in range(GLOW_STEPS + 1):
                r = i / GLOW_STEPS
                alpha = math.exp(-3 * r * r) * (1 - r * r) * glow.opacity  # Gaussian, 0 at r = 1
                spot.add_color_stop_rgba(r, *color, alpha)
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

    def _draw_alert(self, cr, width, height):
        """Someone is talking to the person: an orange frame around the screen, and
        from its top edge hangs a tab saying so (over the text, for a few seconds)."""
        if self._alert == 0:
            return
        frame = min(width, height) * ALERT_FRAME
        cr.push_group()  # drawn whole, then faded as one (overlaps don't get darker)
        cr.set_source_rgb(*ALERT_COLOR)
        cr.set_line_width(frame)
        cr.rectangle(frame / 2, frame / 2, width - frame, height - frame)
        cr.stroke()

        layout = _layout(cr, ALERT_TEXT, markup=False)
        ink, _ = layout.get_pixel_extents()
        scale = min(height * ALERT_LABEL / ink.height, width * 0.8 / ink.width)
        text_width, text_height = ink.width * scale, ink.height * scale
        pad = text_height * 0.5
        tab_width, tab_height = text_width + 4 * pad, frame + text_height + 2 * pad
        _tab(cr, (width - tab_width) / 2, 0, tab_width, tab_height, radius=pad * 1.5)
        cr.fill()

        cr.save()
        cr.translate(width / 2, frame + pad + text_height / 2)
        cr.scale(scale, scale)
        cr.translate(-(ink.x + ink.width / 2), -(ink.y + ink.height / 2))
        cr.set_source_rgb(1, 1, 1)
        PangoCairo.show_layout(cr, layout)
        cr.restore()
        cr.pop_group_to_source()
        cr.paint_with_alpha(self._alert)


def _load_settings() -> _Settings:
    saved = read_json(SETTINGS_FILE) or {}
    settings = _Settings(**{f.name: saved[f.name] for f in fields(_Settings) if f.name in saved})
    settings.names = clean_names(settings.names)
    return settings


def _towards(value, target, step):
    return min(value + step, target) if value < target else max(value - step, target)


def _card(*widgets):
    """A white card in the middle of the screen with the widgets one below the other."""
    card = _styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER,
                           valign=Gtk.Align.CENTER, margin=40), "ui", "card")
    for widget in widgets:
        card.add(widget)
    return card


def _key(label, action):
    """A key of the on-screen keyboard: leaves the focus (and text cursor) in the entry."""
    key = _button(label, action, "key")
    key.set_focus_on_click(False)
    return key


def _tab(cr, x, y, width, height, radius):
    """Path of a rectangle with rounded bottom corners."""
    cr.move_to(x, y)
    cr.line_to(x + width, y)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.close_path()


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
    dx, dy = _unit(angle)
    on_side_edge = abs(dx) * height > abs(dy) * width
    reach = width / 2 / abs(dx) if on_side_edge else height / 2 / abs(dy)
    return width / 2 + dx * reach, height / 2 + dy * reach, on_side_edge


def _unit(angle):
    """x, y of length 1 towards angle (0 = up, clockwise; y grows downwards)."""
    return math.sin(math.radians(angle)), -math.cos(math.radians(angle))


def _rgb(color):
    """0xRRGGBB -> r, g, b in 0..1."""
    return (color >> 16 & 255) / 255, (color >> 8 & 255) / 255, (color & 255) / 255


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
