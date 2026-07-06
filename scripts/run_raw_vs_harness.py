from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.config_loader import DEFAULT_CONFIG_PATH, HarnessConfig, load_config
from agents.generation_controller import GenerationController
from agents.repair_strategy import RepairStrategyAgent
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaModelSupplier
from scripts.run_coding_capability import _behavior_spec, _build_prompt, _final_behavior_issues, _final_static_violations
from validation.behavior import serialize_behavior_result, validate_function_behavior


DEFAULT_TASKS = Path("tests/worker_limit/tasks.json")


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    return sorted(json.loads(path.read_text(encoding="utf-8")), key=lambda task: task.get("difficulty", 0))


def _table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "difficulty",
        "task",
        "raw_behavior",
        "harness_status",
        "harness_static",
        "harness_behavior",
        "attempts",
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
                    row["raw_behavior"],
                    row["harness_status"],
                    str(row["harness_static"]),
                    str(row["harness_behavior"]),
                    str(row["attempts"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def run_raw_vs_harness(
    tasks_path: Path,
    model: str,
    config: HarnessConfig | None = None,
    max_retries: int | None = None,
    limit: int | None = None,
) -> int:
    config = config or HarnessConfig()
    supplier = OllamaModelSupplier(model=model)
    policy = config.engines.policy.to_validation_policy()
    rows: list[dict[str, Any]] = []

    tasks = _load_tasks(tasks_path)
    if limit is not None:
        tasks = tasks[:limit]
    for task in tasks:
        spec = _behavior_spec(task)
        prompt = _build_prompt(task, spec)
        raw_draft = supplier.generate_draft(prompt)
        raw_behavior = serialize_behavior_result(
            validate_function_behavior(
                raw_draft,
                spec,
                timeout_seconds=config.engines.behavior.timeout_seconds,
            )
        )
        controller = GenerationController(
            max_retries=max_retries if max_retries is not None else config.execution.gates.max_retries,
            draft_supplier=lambda _prompt, draft=raw_draft: draft,
            repair_supplier=supplier.repair_draft,
            policy=policy,
            behavior_spec=spec,
            behavior_timeout_seconds=config.engines.behavior.timeout_seconds,
            crosshair_enabled=config.engines.formal.crosshair_enabled,
            crosshair_timeout_seconds=config.engines.formal.crosshair_timeout_seconds,
            repair_strategy=RepairStrategyAgent(),
        )
        result = controller.run(target=task["prompt"], initial_prompt=prompt)
        session = result.payload
        row = {
            "difficulty": task.get("difficulty", ""),
            "task": task["name"],
            "raw_behavior": "pass" if raw_behavior["is_compliant"] else "fail",
            "harness_status": session.get("final_status", ""),
            "harness_static": len(_final_static_violations(session)),
            "harness_behavior": len(_final_behavior_issues(session)),
            "attempts": len(session.get("attempts", [])),
        }
        rows.append(row)
        print(_table(rows), flush=True)
        print("", flush=True)

    print("Final raw-vs-harness table:")
    print(_table(rows))
    raw_passes = sum(1 for row in rows if row["raw_behavior"] == "pass")
    harness_passes = sum(1 for row in rows if row["harness_status"] == "completed")
    print(f"\nRaw behavior pass rate: {raw_passes}/{len(rows)}")
    print(f"Harness final completion rate: {harness_passes}/{len(rows)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare raw one-shot model output against the full harness loop.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    return run_raw_vs_harness(
        tasks_path=args.tasks,
        model=args.model,
        config=config,
        max_retries=args.max_retries,
        limit=args.limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
