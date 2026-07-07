#!/usr/bin/env python3
"""Sample NVIDIA GPU metrics to CSV while a benchmark is running."""

from __future__ import annotations

import argparse
import csv
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


FIELDS = [
    "index",
    "name",
    "utilization.gpu",
    "memory.used",
    "memory.total",
    "power.draw",
    "temperature.gpu",
]


def query_gpu() -> list[dict[str, str]]:
    cmd = [
        "nvidia-smi",
        f"--query-gpu={','.join(FIELDS)}",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    rows: list[dict[str, str]] = []
    for line in result.stdout.strip().splitlines():
        values = [item.strip() for item in line.split(",")]
        rows.append(dict(zip(FIELDS, values, strict=False)))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("results/gpu_metrics.csv"))
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    file_exists = args.output.exists()
    with args.output.open("a", encoding="utf-8", newline="") as f:
        fieldnames = ["timestamp_utc", "label", *FIELDS]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        print(f"[gpu-monitor] writing {args.output}; Ctrl-C to stop")
        try:
            while True:
                timestamp = datetime.now(timezone.utc).isoformat()
                for row in query_gpu():
                    writer.writerow({"timestamp_utc": timestamp, "label": args.label, **row})
                f.flush()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("[gpu-monitor] stopped")


if __name__ == "__main__":
    main()
