from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.historian import HistorianAgent
from benchmarker import HISTORY_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate raw harness run samples into routing stats.")
    parser.add_argument("--runs", default="data/runs.jsonl", help="Append-only JSONL run sample path.")
    parser.add_argument("--stats", default="data/stats.json", help="Aggregated stats JSON output path.")
    args = parser.parse_args()

    stats = HistorianAgent(HISTORY_PATH).aggregate_run_stats(Path(args.runs), Path(args.stats))
    print(json.dumps(stats, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
