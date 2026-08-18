"""Paired coding-agent benchmark with explicit token and outcome accounting."""

from __future__ import annotations

import json
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    category: str
    prompt: str
    suggested_max_turns: int | None = None


@dataclass(frozen=True)
class AgentRunMetrics:
    success: bool
    prompt_tokens: int
    completion_tokens: int
    tool_calls: int
    retries: int
    duration_seconds: float
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class PairedTaskResult:
    task: BenchmarkTask
    baseline: AgentRunMetrics
    shielded: AgentRunMetrics

    @property
    def token_delta(self) -> int:
        return self.baseline.total_tokens - self.shielded.total_tokens


AgentRunner = Callable[[BenchmarkTask], AgentRunMetrics]


def load_tasks(path: Path) -> list[BenchmarkTask]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("benchmark task file must contain a JSON list")
    tasks = []
    for row in payload:
        tasks.append(
            BenchmarkTask(
                task_id=str(row["task_id"]),
                category=str(row["category"]),
                prompt=str(row["prompt"]),
                suggested_max_turns=(
                    int(row["suggested_max_turns"])
                    if row.get("suggested_max_turns") is not None
                    else None
                ),
            )
        )
    return tasks


def command_runner(command: list[str]) -> AgentRunner:
    """Create a runner for a command accepting task JSON on stdin."""

    def run(task: BenchmarkTask) -> AgentRunMetrics:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(asdict(task)),
                text=True,
                capture_output=True,
                timeout=600,
                check=False,
            )
            payload = json.loads(completed.stdout)
            if not isinstance(payload, dict):
                raise ValueError("runner response must be a JSON object")
            return AgentRunMetrics(
                success=bool(payload.get("success")) and completed.returncode == 0,
                prompt_tokens=max(0, int(payload.get("prompt_tokens", 0))),
                completion_tokens=max(0, int(payload.get("completion_tokens", 0))),
                tool_calls=max(0, int(payload.get("tool_calls", 0))),
                retries=max(0, int(payload.get("retries", 0))),
                duration_seconds=time.monotonic() - started,
                error=str(payload.get("error") or ""),
                metadata=(
                    dict(payload["metadata"])
                    if isinstance(payload.get("metadata"), dict)
                    else {}
                ),
            )
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
            return AgentRunMetrics(
                False,
                0,
                0,
                0,
                0,
                time.monotonic() - started,
                f"{type(exc).__name__}: {exc}",
            )

    return run


def run_paired_benchmark(
    tasks: list[BenchmarkTask],
    baseline_runner: AgentRunner,
    shielded_runner: AgentRunner,
) -> dict[str, Any]:
    results = [
        PairedTaskResult(task, baseline_runner(task), shielded_runner(task))
        for task in tasks
    ]
    baseline_tokens = sum(row.baseline.total_tokens for row in results)
    shielded_tokens = sum(row.shielded.total_tokens for row in results)
    baseline_successes = sum(row.baseline.success for row in results)
    shielded_successes = sum(row.shielded.success for row in results)
    deltas = [row.token_delta for row in results]
    return {
        "schema_version": 2,
        "task_count": len(results),
        "baseline_successes": baseline_successes,
        "shielded_successes": shielded_successes,
        "baseline_tokens": baseline_tokens,
        "shielded_tokens": shielded_tokens,
        "token_delta": baseline_tokens - shielded_tokens,
        "token_reduction_ratio": (
            (baseline_tokens - shielded_tokens) / baseline_tokens
            if baseline_tokens
            else 0.0
        ),
        "median_task_token_delta": statistics.median(deltas) if deltas else 0,
        "shielded_regression": _paired_regression(results),
        "results": [
            {
                "task": asdict(row.task),
                "baseline": {**asdict(row.baseline), "total_tokens": row.baseline.total_tokens},
                "shielded": {**asdict(row.shielded), "total_tokens": row.shielded.total_tokens},
                "token_delta": row.token_delta,
            }
            for row in results
        ],
    }


def _paired_regression(results: list[PairedTaskResult]) -> dict[str, int | float | list[str]]:
    baseline_passes = [row for row in results if row.baseline.success]
    regressed = [row.task.task_id for row in baseline_passes if not row.shielded.success]
    return {
        "baseline_successes": len(baseline_passes),
        "regressed_tasks": regressed,
        "count": len(regressed),
        "rate": len(regressed) / len(baseline_passes) if baseline_passes else 0.0,
    }


def run_three_arm_benchmark(
    tasks: list[BenchmarkTask],
    baseline_runner: AgentRunner,
    generic_runner: AgentRunner,
    routed_runner: AgentRunner,
) -> dict[str, Any]:
    """Run no-formal-guidance, generic, and signature-routed repair together.

    This deliberately has its own schema instead of relabelling the paired
    ``shielded`` field.  A three-arm study should be readable from raw JSON
    without relying on command-line provenance to infer what an arm meant.
    """

    runners = {
        "baseline": baseline_runner,
        "generic": generic_runner,
        "routed": routed_runner,
    }
    results: list[dict[str, Any]] = []
    for task in tasks:
        outcomes = {name: runner(task) for name, runner in runners.items()}
        row: dict[str, Any] = {"task": asdict(task)}
        for name, outcome in outcomes.items():
            row[name] = {**asdict(outcome), "total_tokens": outcome.total_tokens}
        results.append(row)

    arm_metrics: dict[str, dict[str, int | float]] = {}
    for name in runners:
        outcomes = [row[name] for row in results]
        arm_metrics[name] = {
            "successes": sum(bool(outcome["success"]) for outcome in outcomes),
            "tokens": sum(int(outcome["total_tokens"]) for outcome in outcomes),
            "tool_calls": sum(int(outcome["tool_calls"]) for outcome in outcomes),
            "duration_seconds": sum(float(outcome["duration_seconds"]) for outcome in outcomes),
        }
    regressions = {
        name: _regression_metrics(results, name)
        for name in ("generic", "routed")
    }
    classified = sum(
        bool(row["routed"].get("metadata", {}).get("repair_route", {}).get("classified"))
        for row in results
    )
    return {
        "schema_version": 3,
        "task_count": len(results),
        "arms": arm_metrics,
        "regressions": regressions,
        "routed_coverage": {
            "classified_tasks": classified,
            "unclassified_tasks": len(results) - classified,
            "rate": classified / len(results) if results else 0.0,
        },
        "results": results,
    }


def _regression_metrics(results: list[dict[str, Any]], arm: str) -> dict[str, int | float | list[str]]:
    """Measure baseline passes that a repair arm turns into failures."""

    baseline_successes = [
        row for row in results if bool(row.get("baseline", {}).get("success"))
    ]
    regressed = [
        str(row.get("task", {}).get("task_id", "unknown"))
        for row in baseline_successes
        if not bool(row.get(arm, {}).get("success"))
    ]
    return {
        "baseline_successes": len(baseline_successes),
        "regressed_tasks": regressed,
        "count": len(regressed),
        "rate": len(regressed) / len(baseline_successes) if baseline_successes else 0.0,
    }
