from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.artifact_manager import ArtifactManager
from agents.generation_controller import GenerationController
from agents.config_loader import DEFAULT_CONFIG_PATH, HarnessConfig, load_config
from agents.historian import HistorianAgent
from agents.plan_mode import PlanModeAgent
from agents.repair_strategy import RepairStrategyAgent
from backends.architect_client import ArchitectModelSupplier
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaModelSupplier
from validation.behavior import BehaviorCase, FunctionBehaviorSpec, format_behavior_spec


DEFAULT_TASKS = Path("tests/coding_capability/tasks.json")
DEFAULT_RUNS = Path("data/runs.jsonl")
DEFAULT_HISTORY = Path("history.json")
DEFAULT_ARTIFACT_ROOT = Path("artifacts/runs")

FIXTURE_SOLUTIONS = {
    "matrix_scoring": """
def _score_value(value):
    return (
        (value < 0) * 1
        + (value == 0) * 2
        + (0 < value < 10) * 3
        + (10 <= value < 100) * 4
        + (value >= 100) * 5
    )


def analyze(matrix):
    return sum(_score_value(value) for row in matrix for value in row)
""".strip(),
    "dedupe_preserve_order": """
def dedupe_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
""".strip(),
    "clamp_values": """
def clamp_values(values, lower, upper):
    return [min(max(value, lower), upper) for value in values]
""".strip(),
    "merge_intervals": """
def merge_intervals(intervals):
    normalized = sorted([list(pair) if pair[0] <= pair[1] else [pair[1], pair[0]] for pair in intervals])
    merged = []
    for start, end in normalized:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged
""".strip(),
    "parse_key_value_lines": """
def _to_value(value):
    stripped = value.strip()
    if stripped.startswith("-"):
        digits = stripped[1:]
    else:
        digits = stripped
    if digits.isdigit():
        return int(stripped)
    return stripped


def parse_key_value_lines(text):
    parsed = {}
    for line in text.splitlines():
        if line.count("=") != 1:
            continue
        key, value = line.split("=")
        key = key.strip()
        if key:
            parsed[key] = _to_value(value)
    return parsed
""".strip(),
    "group_top_scores": """
def group_top_scores(records):
    grouped = {}
    required = {"team", "player", "score"}
    for record in records:
        if not required.issubset(record):
            continue
        grouped.setdefault(record["team"], []).append((record["score"], record["player"]))
    return {
        team: [player for _score, player in sorted(values, key=lambda item: (-item[0], item[1]))[:2]]
        for team, values in grouped.items()
    }
""".strip(),
    "summarize_transactions": """
def summarize_transactions(rows):
    totals = {}
    for row in rows:
        if not {"account", "kind", "amount"}.issubset(row):
            continue
        account = row["account"]
        kind = row["kind"]
        if kind == "credit":
            totals[account] = totals.get(account, 0) + row["amount"]
        elif kind == "debit":
            totals[account] = totals.get(account, 0) - row["amount"]
    return totals
""".strip(),
}


def _load_tasks(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _behavior_spec(task: dict) -> FunctionBehaviorSpec:
    return FunctionBehaviorSpec(
        function_name=task["function_name"],
        cases=[
            BehaviorCase(
                name=case["name"],
                args=tuple(case.get("args", [])),
                kwargs=case.get("kwargs", {}),
                expected=case["expected"],
            )
            for case in task["cases"]
        ],
    )


def _build_prompt(task: dict, spec: FunctionBehaviorSpec) -> str:
    plan_context = _plan_mode_context(task, spec)
    return "\n".join(
        [
            "You are a small coding worker in a Plan-Execute-Verify harness.",
            "Write a complete Python implementation for the requested function.",
            "",
            "Hard requirements:",
            "- Return code only, with no markdown fences or prose.",
            "- Define exactly the requested function and any small private helpers it needs.",
            "- Do not include imports, file I/O, network calls, eval, exec, print calls, demo code, or global mutable state.",
            "- Keep loop nesting at depth 2 or less.",
            "- Keep cyclomatic complexity at 7 or less.",
            "- Prefer precomputed set or dictionary lookups for repeated membership tests.",
            "- Preserve the behavior shown in the unit test specification.",
            "",
            "Task:",
            task["prompt"],
            "",
            plan_context,
            "",
            format_behavior_spec(spec),
            "",
            "Output only Python code.",
        ]
    )


def _plan_mode_context(task: dict, spec: FunctionBehaviorSpec) -> str:
    plan_mode = PlanModeAgent()
    example_lines = []
    for case in spec.cases:
        args = ", ".join(repr(arg) for arg in case.args)
        kwargs = ", ".join(f"{key}={value!r}" for key, value in case.kwargs.items())
        call_args = ", ".join(item for item in [args, kwargs] if item)
        example_lines.append(f"{spec.function_name}({call_args}) == {case.expected!r}")
    plan_prompt = "\n".join([task["prompt"], *example_lines])
    plan = plan_mode.plan(plan_prompt)
    sections = [
        "Compact Plan Mode Packet:",
        plan_mode.to_worker_packet(plan),
    ]
    if plan.deal_contracts:
        worker_examples = [
            contract.removeprefix("@deal.example(lambda: ").removesuffix(")")
            for contract in plan.deal_contracts
            if contract.startswith("@deal.example(lambda: ")
        ]
        if worker_examples:
            sections.extend(
                [
                    "",
                    "Contract examples for the worker:",
                    *[f"- {example}" for example in worker_examples],
                ]
            )
    return "\n".join(sections)


def _all_static_violations(session: dict) -> list[dict]:
    violations: list[dict] = []
    for attempt in session.get("attempts", []):
        violations.extend(attempt.get("validation", {}).get("violations", []))
    return violations


def _all_behavior_issues(session: dict) -> list[dict]:
    issues: list[dict] = []
    for attempt in session.get("attempts", []):
        issues.extend(attempt.get("behavior_validation", {}).get("issues", []))
    return issues


def _final_attempt(session: dict) -> dict:
    attempts = session.get("attempts", [])
    return attempts[-1] if attempts else {}


def _final_static_violations(session: dict) -> list[dict]:
    return _final_attempt(session).get("validation", {}).get("violations", [])


def _final_behavior_issues(session: dict) -> list[dict]:
    return _final_attempt(session).get("behavior_validation", {}).get("issues", [])


def _worker_contribution(session: dict) -> dict[str, Any]:
    """Summarize whether the small worker actually moved the task forward.

    ``repair_worker`` is stored on the attempt that requested the next draft. The
    following attempt is the result of that worker. This helper keeps the summary
    simple enough for the terminal and rich enough for ``runs.jsonl``.
    """

    attempts = session.get("attempts", [])
    completed = session.get("final_status") == "completed"
    repair_workers = [attempt.get("repair_worker", "") for attempt in attempts]
    architect_used = any("architect_llm" in worker for worker in repair_workers)
    small_repair_indices = [
        index
        for index, worker in enumerate(repair_workers)
        if worker == "small_worker"
    ]
    small_repair_count = len(small_repair_indices)
    small_changed_count = sum(
        1
        for index in small_repair_indices
        if index + 1 < len(attempts) and attempts[index + 1].get("changed", False)
    )

    if completed and len(attempts) == 1:
        label = "small_solved_initial"
        score = 1.0
    elif completed and not architect_used:
        label = "small_repaired"
        score = 1.0
    elif completed and architect_used and small_changed_count:
        label = "small_helped_architect"
        score = 0.5
    elif completed and architect_used:
        label = "architect_solved_after_small_stall"
        score = 0.0
    elif small_changed_count:
        label = "small_made_progress_but_failed"
        score = 0.25
    else:
        label = "small_no_progress"
        score = 0.0

    return {
        "label": label,
        "score": score,
        "small_repair_count": small_repair_count,
        "small_changed_count": small_changed_count,
        "architect_used": architect_used,
    }


def _fixture_supplier(task_name: str) -> tuple[Any, Any]:
    solution = FIXTURE_SOLUTIONS[task_name]
    return (lambda _prompt: solution), (lambda _draft, _retry_prompt: solution)


def _ollama_supplier(model: str) -> tuple[Any, Any]:
    supplier = OllamaModelSupplier(model=model)
    return supplier.generate_draft, supplier.repair_draft


def _model_supplier(task: dict, model: str, supplier_mode: str) -> tuple[Any, Any, str]:
    if supplier_mode == "fixture":
        draft_supplier, repair_supplier = _fixture_supplier(task["name"])
        return draft_supplier, repair_supplier, "fixture-supplier"
    draft_supplier, repair_supplier = _ollama_supplier(model)
    return draft_supplier, repair_supplier, model


def run_tasks(
    tasks_path: Path,
    runs_path: Path,
    history_path: Path,
    artifact_root: Path,
    model: str,
    max_retries: int,
    supplier_mode: str,
    record_runs: bool,
    save_artifacts: bool,
    config: HarnessConfig | None = None,
    architect_after_repair_attempts: int | None = None,
) -> int:
    config = config or HarnessConfig()
    policy = config.engines.policy.to_validation_policy()
    behavior_timeout_seconds = config.engines.behavior.timeout_seconds
    os.environ.setdefault("ARCHITECT_MODEL", config.execution.models.architect_model)
    tasks = _load_tasks(tasks_path)
    historian = HistorianAgent(history_path)
    artifact_manager = ArtifactManager(artifact_root)
    passed = 0
    sessions: list[dict] = []

    for task in tasks:
        spec = _behavior_spec(task)
        prompt = _build_prompt(task, spec)
        active_behavior_spec = spec if config.engines.behavior.enabled else None
        draft_supplier, repair_supplier, model_label = _model_supplier(
            task,
            model=model,
            supplier_mode=supplier_mode,
        )
        controller = GenerationController(
            max_retries=max_retries,
            draft_supplier=draft_supplier,
            repair_supplier=repair_supplier,
            architect_supplier=ArchitectModelSupplier().repair_draft
            if architect_after_repair_attempts is not None
            else None,
            architect_after_repair_attempts=architect_after_repair_attempts,
            policy=policy,
            behavior_spec=active_behavior_spec,
            behavior_timeout_seconds=behavior_timeout_seconds,
            crosshair_enabled=config.engines.formal.crosshair_enabled,
            crosshair_timeout_seconds=config.engines.formal.crosshair_timeout_seconds,
            repair_strategy=RepairStrategyAgent(),
        )
        result = controller.run(target=task["prompt"], initial_prompt=prompt)
        session = result.payload
        sessions.append(session)
        completed = session.get("final_status") == "completed"
        if completed:
            passed += 1

        final_static_violations = _final_static_violations(session)
        final_behavior_issues = _final_behavior_issues(session)
        all_static_violations = _all_static_violations(session)
        all_behavior_issues = _all_behavior_issues(session)
        repair_workers = [
            attempt.get("repair_worker", "")
            for attempt in session.get("attempts", [])
            if attempt.get("repair_worker")
        ]
        contribution = _worker_contribution(session)
        artifact_path = ""
        run_id = ""
        if save_artifacts:
            paths = artifact_manager.create_run(prefix=task["name"])
            run_id = paths.run_id
            artifact_path = str(paths.run_dir)
            artifact_manager.save_session(
                session,
                paths,
                metadata={
                    "case_name": task["name"],
                    "model": model_label,
                    "contribution": contribution,
                    "supplier_mode": supplier_mode,
                    "architect_after_repair_attempts": architect_after_repair_attempts,
                },
            )
        worker_summary = ",".join(repair_workers) if repair_workers else "none"
        print(
            f"[coding-capability] {task['name']}: status={session.get('final_status')} "
            f"final_static_violations={len(final_static_violations)} "
            f"final_behavior_issues={len(final_behavior_issues)} "
            f"attempts={len(session.get('attempts', []))} "
            f"model={model_label} "
            f"repair_workers={worker_summary} "
            f"contribution={contribution['label']}:{contribution['score']}"
            f"{' artifacts=' + artifact_path if artifact_path else ''}"
        )
        if all_static_violations or all_behavior_issues:
            print(
                f"  repair history: static_failures={len(all_static_violations)} "
                f"behavior_failures={len(all_behavior_issues)}"
            )
        for violation in final_static_violations[:3]:
            print(
                "  - static "
                f"{violation.get('kind')}: {violation.get('current_value')} "
                f"allowed {violation.get('allowed_value')}"
            )
        for issue in final_behavior_issues[:3]:
            print(
                "  - behavior "
                f"{issue.get('case')}: expected {issue.get('expected')} got {issue.get('actual')}"
            )

        if record_runs:
            run_record = historian.build_run_record(
                session,
                classification={
                    "task_type": task.get("task_type", "general_code"),
                    "language": task.get("language", "python"),
                    "libraries": task.get("libraries", []),
                },
                route_used=session.get("route", ""),
                model=model_label,
                template_name="",
            )
            run_record["case_name"] = task["name"]
            run_record["contribution"] = contribution
            run_record["run_id"] = run_id
            run_record["artifact_path"] = artifact_path
            historian.append_run_sample(runs_path, run_record)

    total = len(tasks)
    print(f"\nCoding capability score: {passed}/{total} completed")
    if record_runs:
        print(f"Run samples appended to {runs_path}")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Test small-worker coding capability with static and behavior gates.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--model", default=None)
    parser.add_argument("--model-profile", default=None)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--supplier", choices=("ollama", "fixture"), default="ollama")
    parser.add_argument("--record-runs", action="store_true")
    parser.add_argument("--save-artifacts", action="store_true")
    parser.add_argument(
        "--architect-after-repair-attempts",
        type=int,
        default=None,
        help=(
            "Escalate repairs to the API-backed architect after this many failed small-worker repair attempts. "
            "Reads DEEPSEEK_API_KEY or ARCHITECT_API_KEY. Defaults to ARCHITECT_MODEL=deepseek-v4-pro."
        ),
    )
    args = parser.parse_args()
    config = load_config(args.config)
    worker_model = args.model or config.execution.models.resolve_worker_model(args.model_profile)
    return run_tasks(
        tasks_path=args.tasks,
        runs_path=args.runs,
        history_path=args.history,
        artifact_root=args.artifact_root,
        model=worker_model or DEFAULT_OLLAMA_MODEL,
        max_retries=(
            args.max_retries
            if args.max_retries is not None
            else config.execution.gates.max_retries
        ),
        supplier_mode=args.supplier,
        record_runs=args.record_runs,
        save_artifacts=args.save_artifacts,
        config=config,
        architect_after_repair_attempts=args.architect_after_repair_attempts,
    )


if __name__ == "__main__":
    raise SystemExit(main())
