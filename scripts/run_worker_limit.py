from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.artifact_manager import ArtifactManager
from agents.config_loader import DEFAULT_CONFIG_PATH, load_config
from agents.generation_controller import GenerationController
from agents.repair_strategy import RepairStrategyAgent
from backends.architect_client import ArchitectModelSupplier
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaGenerationConfig, OllamaModelSupplier
from scripts.run_coding_capability import (
    _all_behavior_issues,
    _all_static_violations,
    _behavior_spec,
    _build_prompt,
    _final_behavior_issues,
    _final_static_violations,
    _worker_contribution,
)


DEFAULT_TASKS = Path("tests/worker_limit/tasks.json")
DEFAULT_DECOMPOSITIONS = Path("tests/worker_limit/decompositions.json")
DEFAULT_ARTIFACT_ROOT = Path("artifacts/runs")


def _keep_first_break(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Keep the earliest failed ladder row when evaluation continues."""

    return current if current is not None else candidate


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    return sorted(json.loads(path.read_text(encoding="utf-8")), key=lambda task: task.get("difficulty", 0))


def _load_decompositions(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _apply_decomposition_prompt(prompt: str, task: dict[str, Any], decomposition: dict[str, str] | None) -> str:
    if not decomposition:
        return prompt
    skeleton = decomposition.get("skeleton", "").strip()
    strategy = decomposition.get("strategy", "manual-decomposition")
    if not skeleton:
        return prompt
    return "\n".join(
        [
            prompt,
            "",
            "DECOMPOSITION MODE:",
            f"- Strategy: {strategy}",
            "- Use the skeleton below as the required structure.",
            "- Replace every pass statement with working code.",
            "- Keep the public function name and helper function names.",
            "- Return the complete Python code only.",
            "",
            "SKELETON TO FILL:",
            skeleton,
        ]
    )


def _table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "difficulty",
        "task",
        "mode",
        "model",
        "status",
        "attempts",
        "small_fail",
        "arch_calls",
        "arch_changed",
        "arch_meaningful",
        "static",
        "behavior",
        "contribution",
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
                    row["mode"],
                    row["model"],
                    row["status"],
                    str(row["attempts"]),
                    str(row["small_fail"]),
                    str(row["arch_calls"]),
                    str(row["arch_changed"]),
                    row["arch_meaningful"],
                    str(row["static"]),
                    str(row["behavior"]),
                    row["contribution"],
                    f"{row['score']:.2f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def run_ladder(
    tasks_path: Path,
    decompositions_path: Path,
    artifact_root: Path,
    model: str,
    max_retries: int,
    num_ctx: int,
    num_predict: int,
    save_artifacts: bool,
    continue_after_failure: bool,
    decompose: bool,
    architect_after_repair_attempts: int | None,
    debug_controller: bool,
) -> int:
    config = load_config(DEFAULT_CONFIG_PATH)
    policy = config.engines.policy.to_validation_policy()
    artifact_manager = ArtifactManager(artifact_root)
    decompositions = _load_decompositions(decompositions_path) if decompose else {}
    rows: list[dict[str, Any]] = []
    first_break: dict[str, Any] | None = None

    for task in _load_tasks(tasks_path):
        spec = _behavior_spec(task)
        active_model = (
            config.execution.models.resolve_for_difficulty(int(task.get("difficulty", 0)))
            if model == "auto"
            else model
        )
        supplier = OllamaModelSupplier(
            model=active_model,
            config=OllamaGenerationConfig(
                temperature=0.1,
                num_ctx=num_ctx,
                num_predict=num_predict,
            ),
        )
        decomposition = decompositions.get(task["name"])
        prompt = _apply_decomposition_prompt(_build_prompt(task, spec), task, decomposition)
        mode = f"decompose:{decomposition.get('strategy', 'manual')}" if decomposition else "direct"
        print(
            f"[worker-limit] difficulty={task['difficulty']} task={task['name']} "
            f"model={active_model} mode={mode} architect_after={architect_after_repair_attempts}",
            flush=True,
        )
        controller = GenerationController(
            max_retries=max_retries,
            draft_supplier=supplier.generate_draft,
            repair_supplier=supplier.repair_draft,
            architect_supplier=ArchitectModelSupplier().repair_draft
            if architect_after_repair_attempts is not None
            else None,
            architect_after_repair_attempts=architect_after_repair_attempts,
            policy=policy,
            behavior_spec=spec,
            behavior_timeout_seconds=config.engines.behavior.timeout_seconds,
            crosshair_enabled=config.engines.formal.crosshair_enabled,
            crosshair_timeout_seconds=config.engines.formal.crosshair_timeout_seconds,
            repair_strategy=RepairStrategyAgent(),
            debug=debug_controller,
        )
        result = controller.run(target=task["prompt"], initial_prompt=prompt)
        session = result.payload
        contribution = _worker_contribution(session)
        final_static = _final_static_violations(session)
        final_behavior = _final_behavior_issues(session)
        all_static = _all_static_violations(session)
        all_behavior = _all_behavior_issues(session)
        artifact_path = ""
        if save_artifacts:
            paths = artifact_manager.create_run(prefix=f"worker_limit_{task['difficulty']}_{task['name']}")
            artifact_path = str(paths.run_dir)
            artifact_manager.save_session(
                session,
                paths,
                metadata={
                    "case_name": task["name"],
                    "difficulty": task["difficulty"],
                    "model": active_model,
                    "contribution": contribution,
                    "benchmark": "worker_limit",
                    "mode": mode,
                    "decomposition": decomposition or {},
                },
            )
        row = {
            "difficulty": task["difficulty"],
            "task": task["name"],
            "mode": mode,
            "status": session.get("final_status", ""),
            "attempts": len(session.get("attempts", [])),
            "small_fail": contribution["small_failed_count"],
            "arch_calls": contribution["architect_repair_count"],
            "arch_changed": contribution["architect_changed_count"],
            "arch_meaningful": "yes" if contribution["architect_meaningful_change"] else "no",
            "static": len(final_static),
            "behavior": len(final_behavior),
            "contribution": contribution["label"],
            "score": float(contribution["score"]),
            "artifact": artifact_path,
            "all_static": len(all_static),
            "all_behavior": len(all_behavior),
            "model": active_model,
        }
        rows.append(row)
        print(_table(rows), flush=True)
        if artifact_path:
            print(f"latest_artifact={artifact_path}", flush=True)
        print("", flush=True)

        if session.get("final_status") != "completed":
            first_break = _keep_first_break(first_break, row)
            if not continue_after_failure:
                break

    print("Final worker-limit table:")
    print(_table(rows))
    if first_break:
        print(
            "\nBreaking point: "
            f"difficulty {first_break['difficulty']} ({first_break['task']}) "
            f"status={first_break['status']} contribution={first_break['contribution']}:{first_break['score']:.2f}"
        )
        return 1
    print("\nNo breaking point found in this ladder.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Push the local worker model through a harder-and-harder task ladder.")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--decompositions", type=Path, default=DEFAULT_DECOMPOSITIONS)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--num-ctx", type=int, default=2048)
    parser.add_argument("--num-predict", type=int, default=384)
    parser.add_argument("--save-artifacts", action="store_true")
    parser.add_argument("--continue-after-failure", action="store_true")
    parser.add_argument("--decompose", action="store_true")
    parser.add_argument("--architect-after-repair-attempts", type=int, default=None)
    parser.add_argument("--debug-controller", action="store_true")
    args = parser.parse_args()
    return run_ladder(
        tasks_path=args.tasks,
        decompositions_path=args.decompositions,
        artifact_root=args.artifact_root,
        model=args.model,
        max_retries=args.max_retries,
        num_ctx=args.num_ctx,
        num_predict=args.num_predict,
        save_artifacts=args.save_artifacts,
        continue_after_failure=args.continue_after_failure,
        decompose=args.decompose,
        architect_after_repair_attempts=args.architect_after_repair_attempts,
        debug_controller=args.debug_controller,
    )


if __name__ == "__main__":
    raise SystemExit(main())
