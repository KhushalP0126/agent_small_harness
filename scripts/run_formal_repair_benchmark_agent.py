"""JSON-stdin runner for a formal-counterexample repair A/B benchmark.

The baseline and guided variants begin with identical broken Python programs.
Their only deliberate difference is whether CrossHair's concrete witness is
included in the repair prompt. It is intended for run_repeated_agent_benchmark.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaGenerationConfig, OllamaModelSupplier
from prompt.retry_builder import build_retry_prompt
from validation.formal import FormalResult, validate_with_crosshair
from validation.types import Violation


BROKEN_SOURCES = {
    "formal-clamp-value": '''
def clamp_value(value: int, lower: int, upper: int) -> int:
    """pre: lower <= upper
    post: _ >= lower and _ <= upper
    """
    return value
'''.strip(),
    "formal-identity": '''
def identity(value: int) -> int:
    """post: _ == value"""
    return 0
'''.strip(),
}


def _formal_violations(result: FormalResult) -> list[Violation]:
    return [
        Violation(
            kind="formal_counterexample",
            engine=f"formal-{result.tool}",
            severity="High",
            summary=issue.summary,
            rationale=issue.details,
            current_value="contract or assertion violation",
            allowed_value="all checkable contracts and assertions hold",
            repair_hint="satisfy_contract",
            evidence={"issue": {"counterexample": issue.counterexample}},
        )
        for issue in result.issues
    ]


def _baseline_prompt(prompt: str) -> str:
    """Pre-registered control: remove only the distinct witness line."""
    return "\n".join(
        line for line in prompt.splitlines() if not line.lstrip().startswith("Formal counterexample:")
    )


def _result(
    success: bool,
    started: float,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    error: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "prompt_tokens": max(0, prompt_tokens),
        "completion_tokens": max(0, completion_tokens),
        "tool_calls": 1,
        "retries": 1,
        "duration_seconds": time.monotonic() - started,
        "error": error,
        "metadata": metadata or {"scope": "python-crosshair-only"},
    }


def run_task(
    task: dict[str, Any], *, mode: str, model: str, timeout_seconds: float
) -> dict[str, Any]:
    started = time.monotonic()
    task_id = str(task.get("task_id", ""))
    source = BROKEN_SOURCES.get(task_id)
    if source is None:
        return _result(False, started, error=f"unknown formal benchmark task: {task_id}")
    formal = validate_with_crosshair(source, timeout_seconds=timeout_seconds)
    if formal.skipped:
        return _result(
            False,
            started,
            error="CrossHair is unavailable; formal-repair effectiveness cannot be measured.",
            metadata={"skipped": True, "scope": "python-crosshair-only"},
        )
    if formal.is_compliant:
        return _result(False, started, error="fixture unexpectedly satisfies its contract")
    prompt = build_retry_prompt(source, _formal_violations(formal))
    if mode == "baseline":
        prompt = _baseline_prompt(prompt)
    supplier = OllamaModelSupplier(model=model)
    try:
        repaired = supplier.repair_draft(source, prompt)
        outcome = validate_with_crosshair(repaired, timeout_seconds=timeout_seconds)
        usage = supplier.telemetry[-1] if supplier.telemetry else {}
        return _result(
            outcome.is_compliant and not outcome.skipped,
            started,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            metadata={
                "mode": mode,
                "provider": "ollama",
                "model": model,
                "context_window": OllamaGenerationConfig().num_ctx,
                "thinking_type": "not_applicable",
                "reasoning_effort": "not_applicable",
                "scope": "python-crosshair-only",
                "counterexample_in_prompt": mode == "guided",
                "counterexample": formal.issues[0].counterexample if formal.issues else "",
                "pricing_basis": usage.get("pricing_basis", "local_unpriced"),
                "final_issues": [issue.summary for issue in outcome.issues],
            },
        )
    except Exception as exc:  # noqa: BLE001 - return benchmark-shaped JSON on model failures
        return _result(False, started, error=f"{exc.__class__.__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "guided"), required=True)
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    task = json.load(sys.stdin)
    print(json.dumps(run_task(task, mode=args.mode, model=args.model, timeout_seconds=args.timeout)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
