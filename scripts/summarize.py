#!/usr/bin/env python3
"""Create aggregate tables and charts from benchmark CSV outputs."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def to_float(value: str | None) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def aggregate_summary(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["framework"], row["scenario"], row["concurrency"])].append(row)
    output: list[dict[str, str]] = []
    metric_names = [
        "error_rate",
        "requests_per_s",
        "output_tokens_per_s",
        "total_tokens_per_s",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
        "ttft_p50_ms",
        "ttft_p95_ms",
        "ttft_p99_ms",
        "output_tokens_mean",
        "json_valid_rate",
    ]
    for (framework, scenario, concurrency), items in sorted(grouped.items()):
        row = {"framework": framework, "scenario": scenario, "concurrency": concurrency}
        for name in metric_names:
            row[name] = f"{mean(to_float(item.get(name)) for item in items):.4f}"
        output.append(row)
    return output


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_metric(rows: list[dict[str, str]], scenario: str, metric: str, output: Path) -> None:
    scenario_rows = [row for row in rows if row["scenario"] == scenario]
    if not scenario_rows:
        return
    by_framework: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in scenario_rows:
        by_framework[row["framework"]].append(row)

    plt.figure(figsize=(11, 6))
    for framework, items in sorted(by_framework.items()):
        items = sorted(items, key=lambda item: int(item["concurrency"]))
        plt.plot(
            [int(item["concurrency"]) for item in items],
            [to_float(item[metric]) for item in items],
            marker="o",
            label=framework,
        )
    plt.xlabel("Concurrency")
    plt.ylabel(metric)
    plt.title(f"{scenario}: {metric}")
    plt.grid(True, alpha=0.3)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(output)
    plt.close()


def summarize_gpu(gpu_csv: Path, output_csv: Path) -> None:
    if not gpu_csv.exists():
        return
    rows = read_csv(gpu_csv)
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row.get("label", ""), row["index"])].append(row)
    output: list[dict[str, str]] = []
    for (label, index), items in sorted(grouped.items()):
        output.append(
            {
                "label": label,
                "gpu_index": index,
                "samples": str(len(items)),
                "gpu_util_avg_pct": f"{mean(to_float(item['utilization.gpu']) for item in items):.4f}",
                "gpu_util_peak_pct": f"{max(to_float(item['utilization.gpu']) for item in items):.4f}",
                "memory_peak_mib": f"{max(to_float(item['memory.used']) for item in items):.4f}",
                "power_avg_w": f"{mean(to_float(item['power.draw']) for item in items):.4f}",
                "temperature_peak_c": f"{max(to_float(item['temperature.gpu']) for item in items):.4f}",
            }
        )
    write_csv(output_csv, output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    summary_csv = args.results_dir / "summary.csv"
    if not summary_csv.exists():
        raise SystemExit(f"missing {summary_csv}")

    aggregate = aggregate_summary(read_csv(summary_csv))
    write_csv(args.results_dir / "aggregate_summary.csv", aggregate)
    summarize_gpu(args.results_dir / "gpu_metrics.csv", args.results_dir / "gpu_summary.csv")

    charts_dir = args.results_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    scenarios = sorted({row["scenario"] for row in aggregate})
    for scenario in scenarios:
        for metric in ["output_tokens_per_s", "ttft_p95_ms", "latency_p95_ms", "error_rate"]:
            plot_metric(aggregate, scenario, metric, charts_dir / f"{scenario}_{metric}.png")
    print(f"[done] wrote aggregate tables and charts under {args.results_dir}")


if __name__ == "__main__":
    main()
