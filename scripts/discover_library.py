from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.library_discovery import LibraryDiscoveryAgent


DEFAULT_PROPOSAL_DIR = ROOT / "data" / "library_proposals"


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover a library surface and write a reviewable proposal.")
    parser.add_argument("library", help="Importable Python package name, e.g. json or pandas.")
    parser.add_argument("--proposal-dir", default=str(DEFAULT_PROPOSAL_DIR))
    args = parser.parse_args()

    agent = LibraryDiscoveryAgent()
    path = agent.write_proposal(args.library, Path(args.proposal_dir))
    payload = json.loads(path.read_text(encoding="utf-8"))
    proposal = payload.get("proposal", {})
    print(f"Wrote proposal: {path}")
    print(f"Available: {payload.get('available')}")
    print(f"Origin: {payload.get('origin', '')}")
    print(f"Candidate allowed calls: {len(proposal.get('allowed_calls', []))}")
    for symbol in proposal.get("allowed_calls", [])[:25]:
        print(f"- {symbol}")
    if len(proposal.get("allowed_calls", [])) > 25:
        print("...")


if __name__ == "__main__":
    main()
