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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=Path("data/compute_shield_tasks_10.json"))
    parser.add_argument(
        "--baseline-command",
        default="python3 scripts/run_ollama_benchmark_agent.py --mode baseline --model qwen2.5-coder:1.5b",
    )
    parser.add_argument(
        "--shielded-command",
        default="python3 scripts/run_ollama_benchmark_agent.py --mode shielded --model qwen2.5-coder:1.5b",
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/compute-shield-10.json"))
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
        "experiment": "compute-shield-frozen-10",
        "commands": {
            "baseline": args.baseline_command,
            "shielded": args.shielded_command,
        },
        "metrics": compute_shield_metrics(rows, phase=3).to_event(),
        "report": report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(event["metrics"], indent=2, sort_keys=True))
    return 0 if report["baseline_successes"] == 10 and report["shielded_successes"] == 10 else 1


if __name__ == "__main__":
    raise SystemExit(main())
