"""State shared by the app (in its container) and board_host.py (on the board itself).

Both run from this folder and exchange small JSON files in .board/:

    screen.json    app -> host  the frame to draw: markup, status, info, addressed, asleep
    mic.json       host -> app  the mic array: direction, muted, time
    touch.json     host -> app  rewritten on every tap on the screen
    settings.json  host -> app  set up on the screen: mic array orientation, names of
                                the person at the screen; deploy.sh keeps it
"""

import colorsys
import json
import os
import time
from pathlib import Path
from typing import NamedTuple

BOARD_DIR = Path(__file__).resolve().parents[1] / ".board"
SCREEN_FILE = BOARD_DIR / "screen.json"
MIC_FILE = BOARD_DIR / "mic.json"
SETTINGS_FILE = BOARD_DIR / "settings.json"
TOUCH_FILE = BOARD_DIR / "touch.json"

MIC_STALE_SECONDS = 3.0  # older mic state means board_host.py stopped updating it
# A few short names: every name listened for costs recognition accuracy on other words.
MAX_NAMES = 6
MAX_NAME_CHARS = 30


class MicState(NamedTuple):
    direction: int | None  # where the current speaker sits, degrees around the array
    muted: bool  # mute button on the array pressed


NO_MIC_ARRAY = MicState(direction=None, muted=False)


def speaker_color(direction: int) -> int:
    """Color of a seat as 0xRRGGBB: the hue goes once around the table."""
    r, g, b = colorsys.hsv_to_rgb(direction % 360 / 360, 1.0, 1.0)
    return (round(r * 255) << 16) | (round(g * 255) << 8) | round(b * 255)


def write_json(path: Path, data: dict) -> None:
    """Write atomically, so the other side never reads half a file."""
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def clean_names(names: object) -> list[str]:
    """Names as typed or loaded: trimmed, at most MAX_NAMES, without empty ones,
    repeats, or the separators of the recognizer's hotword list (, and /)."""
    clean: list[str] = []
    for name in names if isinstance(names, list) else []:
        if not isinstance(name, str):
            continue
        name = " ".join(name.replace(",", " ").replace("/", " ").split())
        name = name[:MAX_NAME_CHARS].strip()
        if name and name.lower() not in (n.lower() for n in clean):
            clean.append(name)
    return clean[:MAX_NAMES]


def read_names() -> list[str]:
    """What the person at the screen is called ("Mary", "Grandma")."""
    return clean_names((read_json(SETTINGS_FILE) or {}).get("names"))


def write_mic_state(state: MicState) -> None:
    write_json(MIC_FILE, {**state._asdict(), "time": time.time()})


def read_mic_state() -> MicState:
    data = read_json(MIC_FILE)
    if not data or time.time() - data.get("time", 0) > MIC_STALE_SECONDS:
        return NO_MIC_ARRAY
    return MicState(data.get("direction"), bool(data.get("muted")))


def write_touch() -> None:
    """Report a tap on the screen."""
    write_json(TOUCH_FILE, {"time": time.time()})


def touched_at() -> int:
    """When the screen was last tapped, as file time (0: never); a new value is a new tap."""
    try:
        return TOUCH_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        return 0
