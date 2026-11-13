"""What the app (in its container) and board_host.py (on the board itself) share.

Both run from this folder and talk through small JSON files in .board/:

    screen.json  app -> board: {"markup", "status", "info"}, the frame to draw
    mic.json     board -> app: {"direction", "muted", "time"}, the mic array state

Speakers are told apart by where they sit: each direction around the table has
its own color, used both for the LED on the mic array and the dot before a word.
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
MIC_STALE_SECONDS = 3.0  # older mic state means board_host.py stopped updating it


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


def write_mic_state(state: MicState) -> None:
    write_json(MIC_FILE, {**state._asdict(), "time": time.time()})


def read_mic_state() -> MicState:
    data = read_json(MIC_FILE)
    if not data or time.time() - data.get("time", 0) > MIC_STALE_SECONDS:
        return NO_MIC_ARRAY
    return MicState(data.get("direction"), bool(data.get("muted")))
