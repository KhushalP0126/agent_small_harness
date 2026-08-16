"""JSON-stdin runner for a formal-counterexample repair A/B benchmark.

The baseline and guided variants begin with identical broken Python programs.
Their only deliberate difference is whether CrossHair's concrete witness is
included in the repair prompt. It is intended for run_repeated_agent_benchmark.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaGenerationConfig, OllamaModelSupplier
from prompt.retry_builder import build_small_worker_retry_prompt
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
    "formal-nonnegative": '''
def nonnegative(value: int) -> int:
    """post: _ >= 0"""
    return value
'''.strip(),
    "formal-absolute": '''
def absolute(value: int) -> int:
    """post: _ == abs(value)"""
    return value
'''.strip(),
    "formal-double": '''
def double(value: int) -> int:
    """post: _ == value * 2"""
    return value
'''.strip(),
    "formal-successor": '''
def successor(value: int) -> int:
    """post: _ > value"""
    return value
'''.strip(),
    "formal-maximum": '''
def maximum(left: int, right: int) -> int:
    """post: _ >= left and _ >= right"""
    return left
'''.strip(),
    "formal-is-even": '''
def is_even(value: int) -> bool:
    """post: _ == (value % 2 == 0)"""
    return True
'''.strip(),
    "formal-order-pair": '''
def ordered_pair(left: int, right: int) -> tuple[int, int]:
    """post: _[0] <= _[1]"""
    return (left, right)
'''.strip(),
    "formal-trim-text": '''
def trim_text(text: str) -> str:
    """post: _ == text.strip()"""
    return text
'''.strip(),
    "formal-prefix-sum": '''
def prefix_sum(count: int) -> int:
    """pre: count >= 0
    post: _ == count * (count + 1) // 2
    """
    total = 0
    for value in range(count):
        total += value
    return total
'''.strip(),
}


GENERAL_FORMAL_DIRECTIVE = (
    "Use the verifier's counterexample as a valid input unless the source declares a precondition "
    "that excludes it. Make the returned value satisfy the postcondition for that exact input; "
    "do not reject it, raise an exception, or add an early-exit branch that the source does not require."
)


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


def _general_formal_prompt(prompt: str) -> str:
    """Replace a task-pattern directive with one verifier-aligned control directive."""

    return re.sub(
        r"(FIX DIRECTIVE:\n).*?(\n\nFINAL RULES:)",
        rf"\1{GENERAL_FORMAL_DIRECTIVE}\2",
        prompt,
        count=1,
        flags=re.DOTALL,
    )


def _formal_failure_text(result: FormalResult) -> str:
    """Keep failure evidence visible in raw benchmark output and reports."""

    rows: list[str] = []
    for issue in result.issues:
        detail = issue.counterexample or issue.details
        detail = _compact_verifier_detail(detail)
        rows.append(f"{issue.summary}: {detail}" if detail else issue.summary)
    return "; ".join(rows) or "formal validation did not produce issue details"


def _compact_verifier_detail(detail: str) -> str:
    """Keep reports readable while retaining the complete verifier output in metadata."""

    normalized = " ".join(detail.split())
    if "Could not import your code:" in normalized:
        exception = re.findall(r"\b(?:[A-Za-z_]+Error|Exception):\s*([^\n]+)", normalized)
        if exception:
            return f"generated candidate could not be imported: {exception[-1]}"
        return "generated candidate could not be imported"
    return normalized


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
    # The compact worker prompt carries the same concrete witness but ends in
    # an explicit code-only contract. The generic retry prompt is intended for
    # a higher-capacity orchestrator and permits explanatory text, which makes
    # a small local model's benchmark output needlessly ambiguous.
    prompt = build_small_worker_retry_prompt(source, _formal_violations(formal))
    if mode == "baseline":
        prompt = _baseline_prompt(prompt)
    elif mode == "general":
        prompt = _general_formal_prompt(prompt)
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
            error="" if outcome.is_compliant and not outcome.skipped else _formal_failure_text(outcome),
            metadata={
                "mode": mode,
                "provider": "ollama",
                "model": model,
                "context_window": OllamaGenerationConfig().num_ctx,
                "thinking_type": "not_applicable",
                "reasoning_effort": "not_applicable",
                "scope": "python-crosshair-only",
                "counterexample_in_prompt": mode != "baseline",
                "counterexample": formal.issues[0].counterexample if formal.issues else "",
                "repair_prompt": prompt,
                "candidate_source": repaired,
                "pricing_basis": usage.get("pricing_basis", "local_unpriced"),
                "final_issues": [issue.summary for issue in outcome.issues],
                "final_counterexamples": [issue.counterexample for issue in outcome.issues if issue.counterexample],
                "final_verifier_output": [issue.details for issue in outcome.issues],
            },
        )
    except Exception as exc:  # noqa: BLE001 - return benchmark-shaped JSON on model failures
        return _result(False, started, error=f"{exc.__class__.__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "guided", "general"), required=True)
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    task = json.load(sys.stdin)
    print(json.dumps(run_task(task, mode=args.mode, model=args.model, timeout_seconds=args.timeout)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
