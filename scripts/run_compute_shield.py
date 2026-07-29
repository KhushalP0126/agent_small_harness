from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from harness_kernel.compute_shield import (
    compute_shield_metrics,
    shield_task_from_artifacts,
)


def _load_metadata(path: str) -> dict[str, Any]:
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / "metadata.json"
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{candidate} must contain a JSON object")
    return payload


def _pairs(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("run arguments must use TASK=PATH")
        task, path = value.split("=", 1)
        if not task.strip() or not path.strip():
            raise ValueError("run arguments must use non-empty TASK=PATH")
        result[task.strip()] = path.strip()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare model-token telemetry from paired harness artifacts."
    )
    parser.add_argument("--baseline-run", action="append", default=[], metavar="TASK=PATH")
    parser.add_argument("--shielded-run", action="append", default=[], metavar="TASK=PATH")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    baseline = _pairs(args.baseline_run)
    shielded = _pairs(args.shielded_run)
    if set(baseline) != set(shielded) or not baseline:
        parser.error("baseline and shielded runs must name the same non-empty task set")
    rows = [
        shield_task_from_artifacts(
            task,
            _load_metadata(baseline[task]),
            _load_metadata(shielded[task]),
        )
        for task in sorted(baseline)
    ]
    event = compute_shield_metrics(rows, phase=3).to_event()
    text = json.dumps(event, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
