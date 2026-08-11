"""Repeat paired benchmarks and summarize their evidence without hiding runs."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import Any

from .e2e_benchmark import AgentRunner, BenchmarkTask, run_paired_benchmark


SUMMARY_METRICS = (
    "baseline_successes",
    "shielded_successes",
    "baseline_tokens",
    "shielded_tokens",
    "token_delta",
    "token_reduction_ratio",
    "baseline_tool_calls",
    "shielded_tool_calls",
    "baseline_duration_seconds",
    "shielded_duration_seconds",
)


def run_repeated_paired_benchmark(
    tasks: Sequence[BenchmarkTask],
    baseline_runner: AgentRunner,
    shielded_runner: AgentRunner,
    *,
    runs: int,
) -> dict[str, Any]:
    """Run the fixed corpus repeatedly and retain every raw benchmark report."""

    if runs < 2:
        raise ValueError("runs must be at least 2 to report a repeated benchmark")
    reports = [
        run_paired_benchmark(list(tasks), baseline_runner, shielded_runner)
        for _ in range(runs)
    ]
    return {
        "schema_version": 1,
        "task_count": len(tasks),
        "run_count": runs,
        "runs": reports,
        "summary": summarize_reports(reports),
    }


def summarize_reports(reports: Sequence[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    """Return descriptive statistics while preserving the full raw reports."""

    if not reports:
        raise ValueError("at least one report is required")
    values = {metric: [] for metric in SUMMARY_METRICS}
    for report in reports:
        derived = _report_metrics(report)
        for metric, value in derived.items():
            values[metric].append(value)
    return {metric: _distribution(series) for metric, series in values.items()}


def _report_metrics(report: dict[str, Any]) -> dict[str, float | int]:
    results = report.get("results", [])
    baseline = [row["baseline"] for row in results]
    shielded = [row["shielded"] for row in results]
    return {
        "baseline_successes": int(report["baseline_successes"]),
        "shielded_successes": int(report["shielded_successes"]),
        "baseline_tokens": int(report["baseline_tokens"]),
        "shielded_tokens": int(report["shielded_tokens"]),
        "token_delta": int(report["token_delta"]),
        "token_reduction_ratio": float(report["token_reduction_ratio"]),
        "baseline_tool_calls": sum(int(row.get("tool_calls", 0)) for row in baseline),
        "shielded_tool_calls": sum(int(row.get("tool_calls", 0)) for row in shielded),
        "baseline_duration_seconds": sum(float(row.get("duration_seconds", 0.0)) for row in baseline),
        "shielded_duration_seconds": sum(float(row.get("duration_seconds", 0.0)) for row in shielded),
    }


def _distribution(values: Sequence[float | int]) -> dict[str, float | int]:
    count = len(values)
    average = statistics.mean(values)
    stdev = statistics.stdev(values) if count > 1 else 0.0
    # A normal-approximation interval is deliberately labelled descriptive:
    # three repeated model runs are evidence of variance, not a significance test.
    margin = 1.96 * stdev / math.sqrt(count) if count > 1 else 0.0
    return {
        "count": count,
        "mean": average,
        "stdev": stdev,
        "min": min(values),
        "max": max(values),
        "descriptive_95pct_low": average - margin,
        "descriptive_95pct_high": average + margin,
    }
