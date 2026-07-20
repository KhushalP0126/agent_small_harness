from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.plan_mode import PlanModeAgent


DEFAULT_TASKS = Path("tests/plan_mode/tasks.json")


def _keep_first_break(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Keep the earliest failed ladder row when evaluation continues."""

    return current if current is not None else candidate


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    return sorted(json.loads(path.read_text(encoding="utf-8")), key=lambda task: task.get("difficulty", 0))


def _contains_all(values: list[str], needles: list[str]) -> bool:
    haystack = "\n".join(values)
    return all(needle in haystack for needle in needles)


def _deal_example_count(payload: dict[str, Any]) -> int:
    return sum(1 for item in payload.get("deal_contracts", []) if item.startswith("@deal.example"))


def _score_plan(payload: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, float, list[str]]:
    checks: list[tuple[str, bool]] = []
    if "language" in expected:
        checks.append(("language", payload.get("language") == expected["language"]))
    if "task_type" in expected:
        checks.append(("task_type", payload.get("task_type") == expected["task_type"]))
    if "target_function" in expected:
        checks.append(("target_function", payload.get("target_function") == expected["target_function"]))
    if "route_hint" in expected:
        checks.append(("route_hint", payload.get("route_hint") == expected["route_hint"]))
    checks.append(
        (
            "behavior_cases",
            len(payload.get("behavior_cases", [])) >= int(expected.get("min_behavior_cases", 0)),
        )
    )
    checks.append(
        (
            "deal_examples",
            _deal_example_count(payload) >= int(expected.get("min_deal_examples", 0)),
        )
    )
    if "needs_user_clarification" in expected:
        checks.append(
            (
                "needs_user_clarification",
                payload.get("needs_user_clarification") is bool(expected["needs_user_clarification"]),
            )
        )
    if expected.get("allowed_libraries"):
        checks.append(
            (
                "allowed_libraries",
                payload.get("allowed_libraries", []) == expected["allowed_libraries"],
            )
        )
    if expected.get("performance_contains"):
        checks.append(
            (
                "performance_constraints",
                _contains_all(payload.get("performance_constraints", []), expected["performance_contains"]),
            )
        )
    if expected.get("security_contains"):
        checks.append(
            (
                "security_constraints",
                _contains_all(payload.get("security_constraints", []), expected["security_contains"]),
            )
        )
    checks.append(
        (
            "state_machine_constraints",
            len(payload.get("state_machine_constraints", [])) >= int(expected.get("min_state_constraints", 0)),
        )
    )
    if expected.get("state_contains"):
        checks.append(
            (
                "state_machine_constraints_content",
                _contains_all(payload.get("state_machine_constraints", []), expected["state_contains"]),
            )
        )
    if expected.get("question_contains"):
        checks.append(
            (
                "questions",
                _contains_all(payload.get("questions", []), expected["question_contains"]),
            )
        )

    failed = [name for name, passed in checks if not passed]
    score = sum(1 for _name, passed in checks if passed) / max(len(checks), 1)
    return not failed, score, failed


def _table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "difficulty",
        "task",
        "status",
        "function",
        "cases",
        "deal",
        "state",
        "clarify",
        "score",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["difficulty"]),
                    row["task"],
                    row["status"],
                    row["function"],
                    str(row["cases"]),
                    str(row["deal"]),
                    str(row["state"]),
                    str(row["clarify"]),
                    f"{row['score']:.2f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def run_plan_ladder(tasks_path: Path, continue_after_failure: bool = False) -> int:
    agent = PlanModeAgent()
    rows: list[dict[str, Any]] = []
    first_break: dict[str, Any] | None = None

    for task in _load_tasks(tasks_path):
        payload = agent.run(task["prompt"]).payload
        passed, score, failed = _score_plan(payload, task.get("expected", {}))
        row = {
            "difficulty": task["difficulty"],
            "task": task["name"],
            "status": "completed" if passed else "failed:" + ",".join(failed),
            "function": payload.get("target_function", ""),
            "cases": len(payload.get("behavior_cases", [])),
            "deal": _deal_example_count(payload),
            "state": len(payload.get("state_machine_constraints", [])),
            "clarify": bool(payload.get("needs_user_clarification")),
            "score": score,
        }
        rows.append(row)
        print(_table(rows), flush=True)
        print("", flush=True)
        if not passed:
            first_break = _keep_first_break(first_break, row)
            if not continue_after_failure:
                break

    print("Final plan-mode ladder table:")
    print(_table(rows))
    if first_break:
        print(
            "\nBreaking point: "
            f"difficulty {first_break['difficulty']} ({first_break['task']}) "
            f"status={first_break['status']} score={first_break['score']:.2f}"
        )
        return 1
    print("\nNo breaking point found in this plan-mode ladder.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Plan Mode extraction on progressively harder prompts.")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--continue-after-failure", action="store_true")
    args = parser.parse_args()
    return run_plan_ladder(args.tasks, continue_after_failure=args.continue_after_failure)


if __name__ == "__main__":
    raise SystemExit(main())
