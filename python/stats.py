"""CPU load and RAM use of the whole board, for the screen corner and the app log.

/proc shows the whole board, also from inside the app container.
"""

import time
from pathlib import Path

PROC_STAT = Path("/proc/stat")
PROC_MEMINFO = Path("/proc/meminfo")


class SystemStats:
    def __init__(self) -> None:
        self._started = time.monotonic()
        self._first_cpu = self._last_cpu = _cpu_times()
        self._load = 0.0  # 0..1, between the last two samples

    def sample(self) -> None:
        """Measure the CPU load since the previous sample."""
        cpu = _cpu_times()
        self._load = _load(self._last_cpu, cpu)
        self._last_cpu = cpu

    def label(self) -> str:
        """Short text for the screen corner."""
        used, total = _ram_gb()
        return (
            f"CPU {self._load * 100:.0f} % · Ø {self._average_load() * 100:.0f} %"
            f" · RAM {used:.1f}/{total:.1f} GB"
        )

    def log_line(self) -> str:
        minutes = (time.monotonic() - self._started) / 60
        used, total = _ram_gb()
        return (
            f"stats: {minutes:.0f} min, CPU now {self._load * 100:.0f} %, "
            f"average {self._average_load() * 100:.1f} %, RAM {used:.2f}/{total:.2f} GB used"
        )

    def _average_load(self) -> float:
        return _load(self._first_cpu, self._last_cpu)


def _load(before: tuple[int, int], after: tuple[int, int]) -> float:
    busy, total = after[0] - before[0], after[1] - before[1]
    return busy / total if total > 0 else 0.0


def _cpu_times() -> tuple[int, int]:
    """(busy, total) jiffies over all CPUs."""
    fields = [int(x) for x in PROC_STAT.read_text().split("\n", 1)[0].split()[1:]]
    idle = fields[3] + fields[4]  # idle + iowait
    total = sum(fields[:8])  # user nice system idle iowait irq softirq steal
    return total - idle, total


def _ram_gb() -> tuple[float, float]:
    """(used, total) RAM in GB; used = total - available."""
    info = {}
    for line in PROC_MEMINFO.read_text().splitlines():
        key, _, value = line.partition(":")
        info[key] = int(value.split()[0])  # kB
    total = info["MemTotal"]
    return (total - info["MemAvailable"]) / 1024**2, total / 1024**2
