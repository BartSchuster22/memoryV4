#!/usr/bin/env python3
"""Silent host-pressure check for MemoryV4 resume decisions."""
from __future__ import annotations

import os
import sys


def meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            key, rest = line.split(":", 1)
            out[key] = int(rest.strip().split()[0])
    return out


def main() -> int:
    load_stop = float(os.environ.get("MEMORYV4_LOAD_STOP", "12"))
    swap_stop = float(os.environ.get("MEMORYV4_SWAP_STOP_PERCENT", "50"))
    load1 = os.getloadavg()[0]
    m = meminfo()
    swap_total = m.get("SwapTotal", 0)
    swap_free = m.get("SwapFree", 0)
    swap_used_pct = 0.0 if swap_total <= 0 else (swap_total - swap_free) * 100.0 / swap_total
    alerts = []
    if load1 > load_stop:
        alerts.append(f"load1 {load1:.2f} > {load_stop:.2f}")
    if swap_used_pct > swap_stop:
        alerts.append(f"swap {swap_used_pct:.1f}% > {swap_stop:.1f}%")
    if alerts:
        print("memoryV4 host-pressure: " + ", ".join(alerts) + "; do not resume more than one card until pressure clears")
    return 0


if __name__ == "__main__":
    sys.exit(main())
