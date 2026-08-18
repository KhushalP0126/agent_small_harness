"""Run the pre-registered no-repair / generic / routed formal-repair study."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness_kernel.e2e_benchmark import AgentRunMetrics, BenchmarkTask, command_runner, load_tasks
from harness_kernel.provenance import collect_provenance
from harness_kernel.research_reporting import run_repeated_three_arm_benchmark
from scripts.run_formal_repair_benchmark_agent import run_task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT / "data" / "formal_repair_diverse_benchmark_tasks.json")
    parser.add_argument("--baseline-command")
    parser.add_argument("--generic-command")
    parser.add_argument("--routed-command")
    parser.add_argument(
        "--in-process",
        action="store_true",
        help="call the formal task runner directly; useful when a sandbox blocks child-process access to local Ollama",
    )
    parser.add_argument("--model", default="qwen2.5-coder:1.5b")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    args = parser.parse_args()

    if args.in_process:
        commands = {
            "baseline": f"in_process formal runner --mode no_repair --model {args.model}",
            "generic": f"in_process formal runner --mode general --model {args.model}",
            "routed": f"in_process formal runner --mode routed --model {args.model}",
        }
        runners = (
            _in_process_runner("no_repair", args.model, args.timeout),
            _in_process_runner("general", args.model, args.timeout),
            _in_process_runner("routed", args.model, args.timeout),
        )
    else:
        if not all((args.baseline_command, args.generic_command, args.routed_command)):
            parser.error("provide all three commands or use --in-process")
        commands = {
            "baseline": args.baseline_command,
            "generic": args.generic_command,
            "routed": args.routed_command,
        }
        runners = tuple(command_runner(shlex.split(command)) for command in commands.values())

    report = run_repeated_three_arm_benchmark(
        load_tasks(args.tasks),
        *runners,
        runs=args.runs,
    )
    payload = {
        "experiment": "failure-mode-routed-formal-repair",
        "commands": commands,
        "provenance": collect_provenance(
            repository_root=args.repository_root,
            task_corpus=args.tasks,
            settings={"runs": args.runs, "execution": "in_process" if args.in_process else "subprocess"},
        ),
        **report,
    }
    payload["variant_metadata"] = _variant_metadata(payload["runs"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


def _variant_metadata(reports: list[dict]) -> dict[str, list[dict]]:
    variants: dict[str, list[dict]] = {"baseline": [], "generic": [], "routed": []}
    for report in reports:
        for result in report.get("results", []):
            for variant in variants:
                metadata = result.get(variant, {}).get("metadata", {})
                if isinstance(metadata, dict) and metadata not in variants[variant]:
                    variants[variant].append(metadata)
    return variants


def _in_process_runner(mode: str, model: str, timeout_seconds: float):
    """Adapt the JSON task function to the common benchmark metric interface."""

    def run(task: BenchmarkTask) -> AgentRunMetrics:
        payload = run_task(
            {
                "task_id": task.task_id,
                "category": task.category,
                "prompt": task.prompt,
            },
            mode=mode,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        return AgentRunMetrics(
            success=bool(payload.get("success")),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            completion_tokens=int(payload.get("completion_tokens", 0)),
            tool_calls=int(payload.get("tool_calls", 0)),
            retries=int(payload.get("retries", 0)),
            duration_seconds=float(payload.get("duration_seconds", 0.0)),
            error=str(payload.get("error") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )

    return run


if __name__ == "__main__":
    raise SystemExit(main())
