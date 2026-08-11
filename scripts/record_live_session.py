"""Write one secret-free receipt for a controlled approval-reviewed session."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness_kernel.live_session import SCENARIOS, build_live_session_receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--prompt-summary", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--validation-status", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--tool-calls", type=int, default=0)
    parser.add_argument("--artifact-reference", default="")
    parser.add_argument("--proposed-diff", type=Path)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    diff = args.proposed_diff.read_text(encoding="utf-8") if args.proposed_diff else ""
    try:
        receipt = build_live_session_receipt(
            repository_root=args.repository_root.resolve(),
            scenario=args.scenario,
            prompt_summary=args.prompt_summary,
            provider=args.provider,
            model=args.model,
            approvals=args.approval,
            validation_status=args.validation_status,
            outcome=args.outcome,
            tool_calls=args.tool_calls,
            artifact_reference=args.artifact_reference,
            proposed_diff=diff,
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
