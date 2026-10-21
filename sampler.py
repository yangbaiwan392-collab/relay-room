#!/usr/bin/env python3
"""Sample GPU memory while a run is in progress; write CSV (ts, used_mib, util)."""
import argparse
import csv
import subprocess
import time


def read_gpu():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=6,
    ).stdout.strip().splitlines()[0]
    used, util = [x.strip() for x in out.split(",")]
    return int(used), int(util)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=600)
    args = ap.parse_args()
    t0 = time.time()
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "used_mib", "util_pct"])
        while time.time() - t0 < args.duration:
            try:
                used, util = read_gpu()
                w.writerow([round(time.time() - t0, 2), used, util])
                fh.flush()
            except Exception:
                pass
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
