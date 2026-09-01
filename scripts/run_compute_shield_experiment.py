"""Run and publish the frozen ten-task Compute Shield comparison."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness_kernel.compute_shield import ShieldTaskTokens, compute_shield_metrics
from harness_kernel.e2e_benchmark import command_runner, load_tasks, run_paired_benchmark
from harness_kernel.provenance import collect_provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT / "data/compute_shield_tasks_10.json")
    parser.add_argument(
        "--baseline-command",
        default="python3 scripts/run_ollama_benchmark_agent.py --mode baseline --model qwen2.5-coder:1.5b",
    )
    parser.add_argument(
        "--shielded-command",
        default="python3 scripts/run_ollama_benchmark_agent.py --mode shielded --model qwen2.5-coder:1.5b",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "docs/results/raw/compute-shield-10-current.json")
    args = parser.parse_args()
    tasks = load_tasks(args.tasks)
    if len(tasks) != 10:
        parser.error("the frozen Compute Shield corpus must contain exactly 10 tasks")
    report = run_paired_benchmark(
        tasks,
        command_runner(shlex.split(args.baseline_command)),
        command_runner(shlex.split(args.shielded_command)),
    )
    rows = [
        ShieldTaskTokens(
            task=result["task"]["task_id"],
            baseline_tokens=int(result["baseline"]["total_tokens"]),
            shielded_tokens=int(result["shielded"]["total_tokens"]),
        )
        for result in report["results"]
    ]
    event = {
        "schema_version": 2,
        "experiment": "compute-shield-frozen-10",
        "commands": {
            "baseline": args.baseline_command,
            "shielded": args.shielded_command,
        },
        "provenance": collect_provenance(
            repository_root=ROOT,
            task_corpus=args.tasks,
            settings={"expected_task_count": 10},
        ),
        "variant_metadata": _variant_metadata(report),
        "metrics": compute_shield_metrics(rows, phase=3).to_event(),
        "report": report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(event["metrics"], indent=2, sort_keys=True))
    return 0 if report["baseline_successes"] == 10 and report["shielded_successes"] == 10 else 1


def _variant_metadata(report: dict) -> dict[str, list[dict]]:
    variants: dict[str, list[dict]] = {"baseline": [], "shielded": []}
    for result in report.get("results", []):
        for variant in variants:
            metadata = result.get(variant, {}).get("metadata", {})
            if isinstance(metadata, dict) and metadata not in variants[variant]:
                variants[variant].append(metadata)
    return variants


if __name__ == "__main__":
    raise SystemExit(main())
