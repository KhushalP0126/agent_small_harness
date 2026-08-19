"""Verify a complete set of secret-free controlled-session receipts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness_kernel.live_session import validate_live_session_receipts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--receipt-dir",
        type=Path,
        required=True,
        help="directory containing one JSON receipt per real controlled session",
    )
    args = parser.parse_args()
    receipt_dir = args.receipt_dir.resolve()
    if not receipt_dir.is_dir():
        parser.error(f"receipt directory does not exist: {receipt_dir}")

    receipts = []
    parse_errors: list[str] = []
    for path in sorted(receipt_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parse_errors.append(f"{path.name}: {exc}")
            continue
        if not isinstance(payload, dict):
            parse_errors.append(f"{path.name}: receipt must be a JSON object")
            continue
        receipts.append(payload)

    result = validate_live_session_receipts(receipts)
    result["receipt_directory"] = str(receipt_dir)
    result["receipt_files"] = len(receipts)
    result["errors"] = [*parse_errors, *result["errors"]]
    result["complete"] = not result["errors"]
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
