from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.formal import validate_with_crosshair


GOOD_SOURCE = """
def clamp_value(value: int, lower: int, upper: int) -> int:
    assert lower <= upper
    result = min(max(value, lower), upper)
    assert lower <= result <= upper
    return result
""".strip()


BAD_SOURCE = """
def clamp_value(value: int, lower: int, upper: int) -> int:
    assert lower <= upper
    result = value
    assert lower <= result <= upper
    return result
""".strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a tiny optional CrossHair experiment.")
    parser.add_argument("--bad", action="store_true", help="Run the intentionally bad sample.")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    source = BAD_SOURCE if args.bad else GOOD_SOURCE
    result = validate_with_crosshair(source, timeout_seconds=args.timeout)
    if result.skipped:
        print("CrossHair skipped: dependency is not installed.")
        return 0
    print(f"CrossHair compliant: {result.is_compliant}")
    for issue in result.issues:
        print(f"- {issue.summary}: {issue.details}")
    return 0 if result.is_compliant or args.bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
