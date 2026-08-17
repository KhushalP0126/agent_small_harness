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
from harness_kernel.provenance import collect_provenance
from harness_kernel.research_reporting import run_repeated_paired_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=ROOT / "data" / "agent_benchmark_tasks.json")
    parser.add_argument("--baseline-command", required=True)
    parser.add_argument("--shielded-command", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument(
        "--require-healthy",
        action="store_true",
        help="write raw evidence but return nonzero when provider failures invalidate comparison aggregation",
    )
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
        "provenance": collect_provenance(
            repository_root=args.repository_root,
            task_corpus=args.tasks,
            settings={"runs": args.runs},
        ),
        **report,
    }
    payload["variant_metadata"] = _variant_metadata(payload["runs"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    if args.require_healthy and not payload["health"]["comparison_eligible"]:
        print(
            "benchmark comparison rejected: " + payload["health"]["reason"],
            file=sys.stderr,
        )
        return 2
    return 0


def _variant_metadata(reports: list[dict]) -> dict[str, list[dict]]:
    """Surface runner-declared model settings without discarding per-task copies."""

    variants: dict[str, list[dict]] = {"baseline": [], "shielded": []}
    for report in reports:
        for result in report.get("results", []):
            for variant in variants:
                metadata = result.get(variant, {}).get("metadata", {})
                if isinstance(metadata, dict) and metadata not in variants[variant]:
                    variants[variant].append(metadata)
    return variants


if __name__ == "__main__":
    raise SystemExit(main())
