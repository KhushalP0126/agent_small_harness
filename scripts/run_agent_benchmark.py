from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness_kernel.e2e_benchmark import command_runner, load_tasks, run_paired_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run identical coding tasks through baseline and local-agent-shielded commands."
    )
    parser.add_argument(
        "--tasks",
        type=Path,
        default=ROOT / "data" / "agent_benchmark_tasks.json",
    )
    parser.add_argument("--baseline-command", required=True)
    parser.add_argument("--shielded-command", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_paired_benchmark(
        load_tasks(args.tasks),
        command_runner(shlex.split(args.baseline_command)),
        command_runner(shlex.split(args.shielded_command)),
    )
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    complete = (
        report["baseline_successes"] == report["task_count"]
        and report["shielded_successes"] == report["task_count"]
    )
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
