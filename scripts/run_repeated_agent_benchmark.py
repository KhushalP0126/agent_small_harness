"""Run a pre-registered paired coding-agent benchmark repeatedly."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness_kernel.e2e_benchmark import command_runner, load_tasks
from harness_kernel.research_reporting import run_repeated_paired_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT / "data" / "agent_benchmark_tasks.json")
    parser.add_argument("--baseline-command", required=True)
    parser.add_argument("--shielded-command", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = run_repeated_paired_benchmark(
        load_tasks(args.tasks),
        command_runner(shlex.split(args.baseline_command)),
        command_runner(shlex.split(args.shielded_command)),
        runs=args.runs,
    )
    payload = {
        "experiment": "repeated-paired-coding-agent-benchmark",
        "commands": {
            "baseline": args.baseline_command,
            "shielded": args.shielded_command,
        },
        **report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
