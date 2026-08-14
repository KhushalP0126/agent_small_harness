"""Show whether recorded route history supports a cost-aware routing claim."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.historian import HistorianAgent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=ROOT / "data" / "runs.jsonl")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stats = HistorianAgent(ROOT / "history.json").aggregate_run_stats(args.runs, args.output)
    groups = []
    evidence_groups = []
    for name, group in stats.get("groups", {}).items():
        routes = group.get("route_metrics", {})
        if len(routes) > 1:
            entry = {"group": name, "routes": routes}
            groups.append(entry)
            eligible = [
                metrics
                for metrics in routes.values()
                if metrics.get("success_rate", 0.0) >= 0.5
                and (
                    metrics.get("token_observations", 0) > 0
                    or metrics.get("cost_observations", 0) > 0
                )
            ]
            if len(eligible) > 1:
                evidence_groups.append(entry)
    print(
        json.dumps(
            {
                "run_samples": stats.get("run_samples", 0),
                "multi_route_groups": groups,
                "routing_evidence_groups": evidence_groups,
                "conclusion": (
                    "Routing evidence is available for at least one comparable multi-route group."
                    if evidence_groups
                    else "No comparable multi-route telemetry yet; routing remains unit-tested but unmeasured in practice."
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
