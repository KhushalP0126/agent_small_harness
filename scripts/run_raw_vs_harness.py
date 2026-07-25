from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.config_loader import DEFAULT_CONFIG_PATH, HarnessConfig, load_config
from agents.artifact_manager import ArtifactManager, ArtifactPaths
from agents.generation_controller import GenerationController
from agents.repair_strategy import RepairStrategyAgent
from backends.architect_client import ArchitectModelSupplier
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaModelSupplier
from scripts.run_coding_capability import _behavior_spec, _build_prompt, _final_behavior_issues, _final_static_violations
from validation.behavior import serialize_behavior_result, validate_function_behavior


DEFAULT_TASKS = Path("tests/worker_limit/tasks.json")


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    return sorted(json.loads(path.read_text(encoding="utf-8")), key=lambda task: task.get("difficulty", 0))


def _naive_repair_prompt(task: dict[str, Any], raw_behavior: dict[str, Any]) -> str:
    """Build the intentionally minimal baseline prompt used by the ablation.

    This baseline gets the observed behavior mismatches and one repair call. It
    does not receive static-engine findings, policy directives, debugger hints,
    retry history, or architect escalation.
    """

    issue_lines = [
        (
            f"- {issue['case']}: expected {issue['expected']}, "
            f"got {issue['actual']} ({issue['details']})"
        )
        for issue in raw_behavior.get("issues", [])
    ]
    return "\n".join(
        [
            "Repair the Python draft so it satisfies the requested behavior.",
            f"Task: {task['prompt']}",
            "Observed failures:",
            *(issue_lines or ["- Behavior validation failed."]),
            "Return only the complete repaired Python code.",
        ]
    )


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    rate = successes / total
    denominator = 1.0 + (z * z / total)
    center = (rate + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt((rate * (1.0 - rate) + z * z / (4.0 * total)) / total)
        / denominator
    )
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _completion_statistics(
    sample_summaries: list[dict[str, Any]],
    pass_key: str,
    total_passes: int,
    total_pairs: int,
) -> dict[str, Any]:
    sample_rates = [
        item[pass_key] / item["task_count"] if item["task_count"] else 0.0
        for item in sample_summaries
    ]
    return {
        "passes": total_passes,
        "total": total_pairs,
        "rate": total_passes / total_pairs if total_pairs else 0.0,
        "sample_rates": sample_rates,
        "sample_rate_variance": (
            statistics.pvariance(sample_rates) if sample_rates else 0.0
        ),
        "sample_rate_stddev": (
            statistics.pstdev(sample_rates) if sample_rates else 0.0
        ),
        "wilson_95_ci": _wilson_interval(total_passes, total_pairs),
    }


def _table(rows: list[dict[str, Any]], include_naive_baseline: bool = False) -> str:
    headers = [
        "sample",
        "difficulty",
        "task",
        "raw_behavior",
    ]
    if include_naive_baseline:
        headers.extend(["naive_behavior", "naive_repair"])
    headers.extend(
        [
            "harness_status",
            "harness_static",
            "harness_behavior",
            "attempts",
            "architect_calls",
        ]
    )
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cells = [
            str(row["sample"]),
            str(row["difficulty"]),
            row["task"],
            row["raw_behavior"],
        ]
        if include_naive_baseline:
            cells.extend(
                [
                    row["naive_behavior"],
                    "yes" if row["naive_repair_used"] else "no",
                ]
            )
        cells.extend(
            [
                row["harness_status"],
                str(row["harness_static"]),
                str(row["harness_behavior"]),
                str(row["attempts"]),
                str(row["architect_calls"]),
            ]
        )
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run_raw_vs_harness(
    tasks_path: Path,
    model: str,
    config: HarnessConfig | None = None,
    max_retries: int | None = None,
    limit: int | None = None,
    architect_after_repair_attempts: int | None = None,
    samples: int = 1,
    include_naive_baseline: bool = False,
    save_artifacts: bool = False,
    artifact_root: Path = Path("artifacts/runs"),
) -> int:
    if samples < 1:
        raise ValueError("samples must be at least 1")
    config = config or HarnessConfig()
    supplier = OllamaModelSupplier(model=model)
    policy = config.engines.policy.to_validation_policy()
    rows: list[dict[str, Any]] = []
    manager = ArtifactManager(artifact_root)
    batch_paths = (
        manager.create_run(prefix="raw_vs_harness")
        if save_artifacts
        else None
    )

    tasks = _load_tasks(tasks_path)
    if limit is not None:
        tasks = tasks[:limit]
    for sample_index in range(1, samples + 1):
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
            naive_draft = raw_draft
            naive_behavior: dict[str, Any] | None = None
            naive_repair_used = False
            if include_naive_baseline:
                if not raw_behavior["is_compliant"]:
                    naive_repair_used = True
                    naive_draft = supplier.repair_draft(
                        raw_draft,
                        _naive_repair_prompt(task, raw_behavior),
                    )
                naive_behavior = serialize_behavior_result(
                    validate_function_behavior(
                        naive_draft,
                        spec,
                        timeout_seconds=config.engines.behavior.timeout_seconds,
                    )
                )
            controller = GenerationController(
                max_retries=max_retries if max_retries is not None else config.execution.gates.max_retries,
                draft_supplier=lambda _prompt, draft=raw_draft: draft,
                repair_supplier=supplier.repair_draft,
                architect_supplier=(
                    ArchitectModelSupplier().repair_draft
                    if architect_after_repair_attempts is not None
                    else None
                ),
                architect_after_repair_attempts=architect_after_repair_attempts,
                policy=policy,
                behavior_spec=spec,
                behavior_timeout_seconds=config.engines.behavior.timeout_seconds,
                crosshair_enabled=config.engines.formal.crosshair_enabled,
                crosshair_timeout_seconds=config.engines.formal.crosshair_timeout_seconds,
                repair_strategy=RepairStrategyAgent(),
                enable_execution_trace=config.engines.behavior.execution_trace,
                enable_debugger_hints=config.engines.behavior.debugger_hints,
                allow_architect_repair_retry=config.execution.routing.allow_architect_repair_retry,
            )
            result = controller.run(target=task["prompt"], initial_prompt=prompt)
            session = result.payload
            attempts = session.get("attempts", [])
            row = {
                "sample": sample_index,
                "difficulty": task.get("difficulty", ""),
                "task": task["name"],
                "raw_behavior": "pass" if raw_behavior["is_compliant"] else "fail",
                "naive_behavior": (
                    "pass"
                    if naive_behavior is not None and naive_behavior["is_compliant"]
                    else "fail"
                    if naive_behavior is not None
                    else "not_run"
                ),
                "naive_repair_used": naive_repair_used,
                "harness_status": session.get("final_status", ""),
                "harness_static": len(_final_static_violations(session)),
                "harness_behavior": len(_final_behavior_issues(session)),
                "attempts": len(attempts),
                "architect_calls": sum(
                    attempt.get("draft_source_worker") == "architect_llm"
                    for attempt in attempts
                ),
            }
            rows.append(row)
            if batch_paths is not None:
                task_dir = (
                    batch_paths.run_dir
                    / f"sample_{sample_index}"
                    / f"{int(task.get('difficulty', 0)):02d}_{task['name']}"
                )
                task_dir.mkdir(parents=True, exist_ok=False)
                task_paths = ArtifactPaths(
                    run_id=f"{batch_paths.run_id}/sample_{sample_index}/{task['name']}",
                    run_dir=task_dir,
                )
                (task_dir / "raw_draft.py").write_text(raw_draft, encoding="utf-8")
                (task_dir / "raw_behavior.json").write_text(
                    json.dumps(raw_behavior, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                if naive_behavior is not None:
                    (task_dir / "naive_draft.py").write_text(
                        naive_draft,
                        encoding="utf-8",
                    )
                    (task_dir / "naive_behavior.json").write_text(
                        json.dumps(naive_behavior, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                manager.save_session(
                    session,
                    task_paths,
                    metadata={
                        "sample": sample_index,
                        "task": task["name"],
                        "difficulty": task.get("difficulty", ""),
                        "model": model,
                        "max_retries": max_retries,
                        "architect_after_repair_attempts": architect_after_repair_attempts,
                        "raw_behavior": raw_behavior,
                        "naive_behavior": naive_behavior,
                        "naive_repair_used": naive_repair_used,
                    },
                )
            print(
                _table(rows, include_naive_baseline=include_naive_baseline),
                flush=True,
            )
            print("", flush=True)

    print("Final raw-vs-harness table:")
    print(_table(rows, include_naive_baseline=include_naive_baseline))
    raw_passes = sum(1 for row in rows if row["raw_behavior"] == "pass")
    naive_passes = sum(1 for row in rows if row["naive_behavior"] == "pass")
    harness_passes = sum(1 for row in rows if row["harness_status"] == "completed")
    recovered = sum(
        1
        for row in rows
        if row["raw_behavior"] == "fail" and row["harness_status"] == "completed"
    )
    sample_summaries = []
    for sample_index in range(1, samples + 1):
        sample_rows = [row for row in rows if row["sample"] == sample_index]
        sample_summaries.append(
            {
                "sample": sample_index,
                "task_count": len(sample_rows),
                "raw_passes": sum(row["raw_behavior"] == "pass" for row in sample_rows),
                "naive_passes": sum(
                    row["naive_behavior"] == "pass" for row in sample_rows
                ),
                "naive_repair_calls": sum(
                    bool(row["naive_repair_used"]) for row in sample_rows
                ),
                "harness_passes": sum(
                    row["harness_status"] == "completed" for row in sample_rows
                ),
                "recovered": sum(
                    row["raw_behavior"] == "fail"
                    and row["harness_status"] == "completed"
                    for row in sample_rows
                ),
            }
        )
    raw_statistics = _completion_statistics(
        sample_summaries,
        "raw_passes",
        raw_passes,
        len(rows),
    )
    harness_statistics = _completion_statistics(
        sample_summaries,
        "harness_passes",
        harness_passes,
        len(rows),
    )
    naive_statistics = (
        _completion_statistics(
            sample_summaries,
            "naive_passes",
            naive_passes,
            len(rows),
        )
        if include_naive_baseline
        else None
    )
    aggregate = {
        "model": model,
        "samples": samples,
        "tasks_per_sample": len(tasks),
        "total_pairs": len(rows),
        "raw_passes": raw_passes,
        "harness_passes": harness_passes,
        "recovered": recovered,
        "raw_pass_rate": raw_passes / len(rows) if rows else 0.0,
        "naive_baseline_enabled": include_naive_baseline,
        "naive_passes": naive_passes if include_naive_baseline else None,
        "naive_repair_calls": (
            sum(bool(row["naive_repair_used"]) for row in rows)
            if include_naive_baseline
            else None
        ),
        "naive_recovered": (
            sum(
                row["raw_behavior"] == "fail"
                and row["naive_behavior"] == "pass"
                for row in rows
            )
            if include_naive_baseline
            else None
        ),
        "naive_pass_rate": (
            naive_passes / len(rows)
            if include_naive_baseline and rows
            else None
        ),
        "harness_pass_rate": harness_passes / len(rows) if rows else 0.0,
        "completion_lift": (harness_passes - raw_passes) / len(rows) if rows else 0.0,
        "harness_lift_over_naive": (
            (harness_passes - naive_passes) / len(rows)
            if include_naive_baseline and rows
            else None
        ),
        "statistics": {
            "raw": raw_statistics,
            "naive": naive_statistics,
            "harness": harness_statistics,
        },
        "sample_raw_pass_range": [
            min((item["raw_passes"] for item in sample_summaries), default=0),
            max((item["raw_passes"] for item in sample_summaries), default=0),
        ],
        "sample_harness_pass_range": [
            min((item["harness_passes"] for item in sample_summaries), default=0),
            max((item["harness_passes"] for item in sample_summaries), default=0),
        ],
        "sample_summaries": sample_summaries,
        "rows": rows,
    }
    print(f"\nRaw behavior pass rate: {raw_passes}/{len(rows)}")
    if include_naive_baseline:
        print(f"Naive one-repair pass rate: {naive_passes}/{len(rows)}")
    print(f"Harness final completion rate: {harness_passes}/{len(rows)}")
    print(f"Recovered raw failures: {recovered}/{len(rows)}")
    print(
        "Per-sample completion range: "
        f"raw {aggregate['sample_raw_pass_range'][0]}-{aggregate['sample_raw_pass_range'][1]}/{len(tasks)}, "
        f"harness {aggregate['sample_harness_pass_range'][0]}-{aggregate['sample_harness_pass_range'][1]}/{len(tasks)}"
    )
    print(
        "Sample-rate standard deviation: "
        f"raw {raw_statistics['sample_rate_stddev']:.4f}, "
        + (
            f"naive {naive_statistics['sample_rate_stddev']:.4f}, "
            if naive_statistics is not None
            else ""
        )
        + f"harness {harness_statistics['sample_rate_stddev']:.4f}"
    )
    if batch_paths is not None:
        summary_path = batch_paths.run_dir / "raw_vs_harness_summary.json"
        summary_path.write_text(
            json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Artifact batch: {batch_paths.run_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare raw one-shot model output against the full harness loop.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--architect-after-repair-attempts", type=int, default=None)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument(
        "--include-naive-baseline",
        action="store_true",
        help="Add one behavior-only repair call without harness diagnostics or escalation.",
    )
    parser.add_argument("--save-artifacts", action="store_true")
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/runs"))
    args = parser.parse_args()
    config = load_config(args.config)
    return run_raw_vs_harness(
        tasks_path=args.tasks,
        model=args.model,
        config=config,
        max_retries=args.max_retries,
        limit=args.limit,
        architect_after_repair_attempts=args.architect_after_repair_attempts,
        samples=args.samples,
        include_naive_baseline=args.include_naive_baseline,
        save_artifacts=args.save_artifacts,
        artifact_root=args.artifact_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
