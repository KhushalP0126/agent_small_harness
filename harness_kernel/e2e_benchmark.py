"""Paired coding-agent benchmark with explicit token and outcome accounting."""

from __future__ import annotations

import json
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    category: str
    prompt: str


@dataclass(frozen=True)
class AgentRunMetrics:
    success: bool
    prompt_tokens: int
    completion_tokens: int
    tool_calls: int
    retries: int
    duration_seconds: float
    error: str = ""

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
