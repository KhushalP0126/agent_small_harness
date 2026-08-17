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


# These failures describe the provider or its local serving process, not a
# candidate's ability to solve a benchmark task.  They must never be averaged
# into a quality or efficiency comparison.
_PROVIDER_FAILURES: tuple[tuple[str, str], ...] = (
    ("architect_empty_response", "architect api returned an empty response"),
    ("architect_malformed_response", "architect api returned a malformed response"),
    ("architect_timeout", "architect api timed out"),
    ("architect_unreachable", "architect api is not reachable"),
    ("architect_http_5xx", "architect api failed with http 5"),
    ("ollama_startup_timeout", "timed out waiting for llama-server to start"),
    ("ollama_unreachable", "ollama is not reachable"),
    ("ollama_http_5xx", "ollama generate failed with http 5"),
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
    health = benchmark_health(reports)
    return {
        "schema_version": 3,
        "task_count": len(tasks),
        "run_count": runs,
        "runs": reports,
        "health": health,
        # Preserve a complete operational summary for diagnostics, but only
        # expose it as a comparison when every repeated run is provider-healthy.
        "summary": summarize_reports(reports),
        "comparison_summary": summarize_reports(reports) if health["comparison_eligible"] else None,
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


def benchmark_health(reports: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Classify provider/infrastructure failures before comparison aggregation."""

    run_statuses: list[dict[str, Any]] = []
    all_failures: list[dict[str, Any]] = []
    for run_number, report in enumerate(reports, start=1):
        failures = _provider_failures(report, run_number)
        all_failures.extend(failures)
        run_statuses.append(
            {
                "run": run_number,
                "eligible": not failures,
                "provider_failures": failures,
            }
        )
    return {
        "comparison_eligible": not all_failures,
        "reason": (
            "all provider calls produced usable responses"
            if not all_failures
            else "provider or local-model infrastructure failures were recorded; comparison aggregation is rejected"
        ),
        "provider_failure_count": len(all_failures),
        "runs": run_statuses,
    }


def _provider_failures(report: dict[str, Any], run_number: int) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for result in report.get("results", []):
        task = str(result.get("task", {}).get("task_id", "unknown"))
        for variant in ("baseline", "shielded"):
            outcome = result.get(variant, {})
            if not isinstance(outcome, dict) or outcome.get("success", False):
                continue
            error = str(outcome.get("error") or "")
            failure_kind = _provider_failure_kind(error)
            if failure_kind:
                failures.append(
                    {
                        "run": run_number,
                        "task_id": task,
                        "variant": variant,
                        "kind": failure_kind,
                        "error": error,
                    }
                )
    return failures


def _provider_failure_kind(error: str) -> str:
    normalized = error.casefold()
    for kind, marker in _PROVIDER_FAILURES:
        if marker in normalized:
            return kind
    return ""


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
