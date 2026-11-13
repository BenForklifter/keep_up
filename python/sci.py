"""Carbon footprint of keep_up after the Green Software Foundation's SCI spec.

    SCI = (E * I + M) / R

E  energy used: estimated from the board's CPU load with a linear power model
   (no power sensor on the UNO Q), P = P_IDLE + (P_FULL - P_IDLE) * load
I  carbon intensity of the electricity, gCO2e/kWh (static: the board runs offline)
M  embodied emissions of the hardware, time-shared over its lifetime
R  functional unit: one hour of keep_up running at the table

    meter = CarbonMeter()
    meter.sample()      # every few seconds
    meter.label()       # "≈2.4 W · 0.9 g CO₂e/h" for the screen

Calibrate P_IDLE_W / P_FULL_W once with a USB-C power meter (or the laptop battery):
board idle vs. `stress -c 4`. Until then they are placeholders.
"""

import time
from pathlib import Path

# --- assumptions: fill in / calibrate -------------------------------------------
P_IDLE_W = 2.0  # PLACEHOLDER: whole board (incl. mic array), CPU idle
P_FULL_W = 5.0  # PLACEHOLDER: whole board, all 4 cores busy
GRID_G_PER_KWH = 380.0  # Germany, grid average (UBA, recent years); check current value
EMBODIED_KG = None  # hardware manufacturing, kgCO2e (board + mic array + monitor); None = unknown
LIFETIME_YEARS = 5.0  # over which the embodied emissions are spread
# ----------------------------------------------------------------------------------

PROC_STAT = Path("/proc/stat")  # host-wide even inside the app container
PROC_MEMINFO = Path("/proc/meminfo")  # likewise: the whole board's RAM


class CarbonMeter:
    def __init__(self):
        self._start = time.monotonic()
        self._last_time = self._start
        self._last_cpu = self._start_cpu = _cpu_times()
        self._energy_wh = 0.0
        self._power_w = P_IDLE_W
        self._load = 0.0  # measured CPU load of the whole board, 0..1, last sample

    def sample(self) -> None:
        """Measure the CPU load and account the (estimated) energy since the last sample."""
        now, cpu = time.monotonic(), _cpu_times()
        busy = cpu[0] - self._last_cpu[0]
        total = cpu[1] - self._last_cpu[1]
        load = self._load = busy / total if total > 0 else 0.0
        self._power_w = P_IDLE_W + (P_FULL_W - P_IDLE_W) * load
        self._energy_wh += self._power_w * (now - self._last_time) / 3600
        self._last_time, self._last_cpu = now, cpu

    def per_hour_g(self) -> tuple[float, float | None]:
        """(operational gCO2e per hour, SCI gCO2e per hour or None while M is unknown)."""
        hours = max(self._last_time - self._start, 1.0) / 3600
        operational = self._energy_wh / 1000 * GRID_G_PER_KWH / hours
        if EMBODIED_KG is None:
            return operational, None
        embodied = EMBODIED_KG * 1000 / (LIFETIME_YEARS * 365 * 24)
        return operational, operational + embodied

    def average_load(self) -> float:
        """Measured CPU load of the whole board since start, 0..1."""
        busy = self._last_cpu[0] - self._start_cpu[0]
        total = self._last_cpu[1] - self._start_cpu[1]
        return busy / total if total > 0 else 0.0

    def label(self) -> str:
        """Short text for the screen corner: measured values only (no estimates yet)."""
        used, total = _ram_gb()
        return (f"CPU {self._load * 100:.0f} % · Ø {self.average_load() * 100:.0f} %"
                f" · RAM {used:.1f}/{total:.1f} GB")

    def log_line(self) -> str:
        minutes = (self._last_time - self._start) / 60
        used, total = _ram_gb()
        return (f"[CPU] {minutes:.0f} min: now {self._load * 100:.0f} %, "
                f"average {self.average_load() * 100:.1f} % (whole board, 4 cores), "
                f"RAM {used:.2f}/{total:.2f} GB used")


def _cpu_times() -> tuple[int, int]:
    """(busy, total) jiffies over all CPUs."""
    fields = [int(x) for x in PROC_STAT.read_text().split("\n", 1)[0].split()[1:]]
    idle = fields[3] + fields[4]  # idle + iowait
    total = sum(fields[:8])  # user nice system idle iowait irq softirq steal
    return total - idle, total


def _ram_gb() -> tuple[float, float]:
    """(used, total) RAM of the whole board in GB; used = total - available."""
    info = {}
    for line in PROC_MEMINFO.read_text().splitlines():
        key, _, value = line.partition(":")
        info[key] = int(value.split()[0])  # kB
    total = info["MemTotal"]
    return (total - info["MemAvailable"]) / 1024**2, total / 1024**2
