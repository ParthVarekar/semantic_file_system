"""How much of the computer may indexing use right now?

A ``Governor`` samples free memory, total CPU load and free GPU memory every few seconds and turns
them into a worker count for each kind of work, always leaving a reserve for Windows and whatever
else the user is doing. It is consulted by every worker pool (file readers, image workers, GPU
embedding workers): pools grow by one worker at a time when there is headroom and shrink as soon
as the machine gets busy, so a game or a big compile started mid-index gets its resources back.

Measured costs per worker (2026-10-27, Core 7 240H, RTX 5050 8 GB, bge-small int8 / nomic-q):
  GPU embedding worker  ~1.0 GB GPU memory, ~0.5 GB RAM   (1 -> 2 -> 3 workers: 1.00x / 1.39x / 1.55x)
  image worker          ~0.3 GB RAM, ``vision_threads`` CPU threads
  file reader           ~0.1-0.3 GB RAM, about one CPU thread
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)
GB = 1024**3

# How much stays free for everything else, per speed setting.
#                 CPU kept free   RAM kept free (fraction, at least)   GPU memory kept free
PROFILES = {
    "gentle": dict(cpu=0.50, ram_frac=0.30, ram_min=4 * GB, vram_frac=0.50),
    "balanced": dict(cpu=0.25, ram_frac=0.20, ram_min=3 * GB, vram_frac=0.25),
    "fast": dict(cpu=0.12, ram_frac=0.12, ram_min=2 * GB, vram_frac=0.15),
}


@dataclass
class Cost:
    ram: int  # bytes per worker
    vram: int = 0  # bytes of GPU memory per worker
    cpu: float = 1.0  # CPU threads per worker
    maximum: int = 3


COSTS = {
    "gpu_embed": Cost(ram=int(0.6 * GB), vram=int(1.05 * GB), cpu=1.0, maximum=3),
    "image": Cost(ram=int(0.35 * GB), cpu=4.0, maximum=3),
    "reader": Cost(ram=int(0.25 * GB), cpu=1.0, maximum=8),
}


@dataclass
class Snapshot:
    cpu_busy: float  # 0..1, whole machine
    ram_free: int
    ram_total: int
    vram_used: int | None = None
    vram_total: int | None = None
    at: float = 0.0


class Governor:
    """Thread-safe; one per process is enough (``governor()``)."""

    def __init__(self, profile: str = "balanced", interval: float = 2.0):
        self.profile = PROFILES.get(profile, PROFILES["balanced"])
        self.interval = interval
        self._lock = threading.Lock()
        self._snap: Snapshot | None = None
        self._cpu_prev: tuple[int, int] | None = None
        self._nvsmi = True

    # ---- measuring --------------------------------------------------------------------
    def snapshot(self) -> Snapshot:
        with self._lock:
            now = time.monotonic()
            if self._snap is None or now - self._snap.at >= self.interval:
                free, total = _memory()
                vu, vt = self._gpu_memory()
                self._snap = Snapshot(self._cpu_busy(), free, total, vu, vt, now)
            return self._snap

    def _cpu_busy(self) -> float:
        t = _cpu_times()
        if t is None:
            try:
                return min(1.0, os.getloadavg()[0] / (os.cpu_count() or 1))
            except (AttributeError, OSError):
                return 0.5
        prev, self._cpu_prev = self._cpu_prev, t
        if prev is None:
            time.sleep(0.2)
            return self._cpu_busy()
        idle, total = t[0] - prev[0], t[1] - prev[1]
        return 0.0 if total <= 0 else max(0.0, min(1.0, 1 - idle / total))

    def _gpu_memory(self) -> tuple[int | None, int | None]:
        if not self._nvsmi:
            return None, None
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            used, total = (int(x) for x in out.splitlines()[0].split(","))
            return used * 1024**2, total * 1024**2
        except Exception:
            self._nvsmi = False  # not an NVIDIA GPU (or no driver tools): rely on the other limits
            return None, None

    # ---- deciding -----------------------------------------------------------------------
    def workers(self, kind: str, current: int, ceiling: int | None = None) -> int:
        """How many workers of ``kind`` may run now, moving at most one step from ``current``.
        Shrinking is immediate when a reserve is breached; growing needs clear headroom."""
        c = COSTS[kind]
        top = min(c.maximum, ceiling or c.maximum)
        s = self.snapshot()
        p = self.profile
        ram_reserve = max(p["ram_min"], p["ram_frac"] * s.ram_total)
        cpus = os.cpu_count() or 4
        cpu_limit = 1 - p["cpu"]
        vram_cap = s.vram_total * (1 - p["vram_frac"]) if s.vram_total else None
        gpu_over = bool(c.vram and vram_cap is not None and s.vram_used > vram_cap)
        gpu_room = not c.vram or vram_cap is None or s.vram_used + c.vram <= vram_cap
        # Shrink as soon as a reserve is breached; grow only with clear headroom everywhere. The gap
        # between the two CPU thresholds keeps a pool from flapping because of its own load.
        if s.ram_free < ram_reserve or s.cpu_busy > cpu_limit + 0.08 or gpu_over:
            target = current - 1
        elif s.ram_free - c.ram > ram_reserve and s.cpu_busy + c.cpu / cpus < cpu_limit and gpu_room:
            target = current + 1
        else:
            target = current
        if s.vram_total is None and c.vram:
            top = min(top, 2)  # GPU memory unknown (non-NVIDIA): stay modest
        return max(1, min(top, target))

    def describe(self) -> str:
        s = self.snapshot()
        g = f", GPU memory {s.vram_used / GB:.1f}/{s.vram_total / GB:.1f} GB" if s.vram_total else ""
        return f"CPU {s.cpu_busy:.0%}, free RAM {s.ram_free / GB:.1f}/{s.ram_total / GB:.1f} GB{g}"


_GOV: Governor | None = None


def governor(profile: str = "balanced") -> Governor:
    global _GOV
    if _GOV is None or _GOV.profile is not PROFILES.get(profile, PROFILES["balanced"]):
        _GOV = Governor(profile)
    return _GOV


# ---- platform probes ------------------------------------------------------------------------
def _memory() -> tuple[int, int]:
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return int(m.ullAvailPhys), int(m.ullTotalPhys)
    try:
        pages, avail = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_AVPHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return avail * size, pages * size
    except (ValueError, OSError, AttributeError):
        return 8 * GB, 16 * GB


def _cpu_times() -> tuple[int, int] | None:
    """(idle, total) 100-ns ticks since boot, all CPUs together (Windows)."""
    if sys.platform != "win32":
        return None
    idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return idle.value, kernel.value + user.value  # kernel time includes idle time
