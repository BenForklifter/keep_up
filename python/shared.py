"""What the app (in its container) and board_host.py (on the board itself) share.

Both run from this folder and talk through small JSON files in .board/:

    screen.json    app -> board: {"markup", "status", "info", "addressed", "asleep"}, the
                   frame to draw; asleep: the sleep mode (presence.py), dark screen
    mic.json       board -> app: {"direction", "muted", "time"}, the mic array state
    touch.json     board -> app: {"time"}, the screen was tapped: someone is there (wakes
                   the app from its sleep mode); written anew with every tap
    settings.json  board -> app: what is set up on the screen itself (screen.py): which
                   way the glow towards the speaker points, and the names of the person
                   at the screen, which the app listens for. deploy.sh keeps it across deploys.

Speakers are told apart by where they sit: each direction around the table has
its own color for the LED on the mic array that lights up towards them.
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
MAX_NAMES = 6  # names of the person at the screen: a few (a long list costs the
MAX_NAME_CHARS = 30  # speech recognition accuracy on all other words), each short


class MicState(NamedTuple):
    direction: int | None  # where the current speaker sits, degrees around the array
    muted: bool  # mute button on the array pressed


NO_MIC_ARRAY = MicState(direction=None, muted=False)


def speaker_color(direction: int) -> int:
    """0xRRGGBB for a speaker direction: the hue goes once around the color wheel."""
    r, g, b = colorsys.hsv_to_rgb(direction % 360 / 360, 1.0, 1.0)
    return (round(r * 255) << 16) | (round(g * 255) << 8) | round(b * 255)


def write_json(path: Path, data: dict) -> None:
    """Atomic write, so the other side never reads half a file."""
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def clean_names(names) -> list[str]:
    """Names as typed or saved -> trimmed, without empty ones, repeats or commas
    (speech recognition separates its terms with them), at most MAX_NAMES."""
    clean = []
    for name in names if isinstance(names, list) else []:
        if isinstance(name, str):
            name = " ".join(name.replace(",", " ").split())[:MAX_NAME_CHARS].strip()
            if name and name.lower() not in (n.lower() for n in clean):
                clean.append(name)
    return clean[:MAX_NAMES]


def read_names() -> list[str]:
    """What the person at the screen is called ("Mary", "Grandma"), set on the screen."""
    return clean_names((read_json(SETTINGS_FILE) or {}).get("names"))


def write_mic_state(state: MicState) -> None:
    write_json(MIC_FILE, {**state._asdict(), "time": time.time()})


def read_mic_state() -> MicState:
    data = read_json(MIC_FILE)
    if not data or time.time() - data.get("time", 0) > MIC_STALE_SECONDS:
        return NO_MIC_ARRAY
    return MicState(data.get("direction"), bool(data.get("muted")))


def write_touch() -> None:
    """The screen was tapped (screen.py)."""
    write_json(TOUCH_FILE, {"time": time.time()})


def touched_at() -> int:
    """When the screen was last tapped, as file time (0: not since the deploy);
    a new value means a new tap."""
    try:
        return TOUCH_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        return 0
