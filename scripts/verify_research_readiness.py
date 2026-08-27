"""Evaluate and render the authoritative research-readiness gate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness_kernel.research_readiness import (
    evaluate_research_readiness,
    render_readiness_markdown,
    render_readiness_svg,
)


def _run_check(command: list[str]) -> dict[str, object]:
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    except OSError as exc:
        return {
            "command": command,
            "passed": False,
            "exit_code": None,
            "output_tail": f"{type(exc).__name__}: {exc}",
        }
    return {
        "command": command,
        "passed": result.returncode == 0,
        "exit_code": result.returncode,
        "output_tail": (result.stdout + result.stderr)[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-checks", action="store_true")
    parser.add_argument("--json-output", type=Path, default=ROOT / "docs/results/raw/research-readiness.json")
    parser.add_argument("--report-output", type=Path, default=ROOT / "docs/results/research-readiness.md")
    parser.add_argument("--svg-output", type=Path, default=ROOT / "docs/results/research-readiness.svg")
    args = parser.parse_args()
    if args.record_checks:
        verification = {
            "schema_version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "python_tests": _run_check([sys.executable, "-m", "pytest", "-q", "tests"]),
            "rust_tests": _run_check(["cargo", "test", "--manifest-path", "rust_tui/Cargo.toml"]),
        }
        verification_path = ROOT / "docs/results/raw/readiness/verification.json"
        verification_path.parent.mkdir(parents=True, exist_ok=True)
        verification_path.write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")

    # Render once, then re-evaluate so the visualization gate describes the
    # files produced by this invocation rather than its pre-render state.
    result = evaluate_research_readiness(ROOT)
    args.svg_output.parent.mkdir(parents=True, exist_ok=True)
    args.svg_output.write_text(render_readiness_svg(result) + "\n", encoding="utf-8")
    args.report_output.write_text(
        render_readiness_markdown(result, svg_name=args.svg_output.name),
        encoding="utf-8",
    )
    result = evaluate_research_readiness(ROOT)
    args.svg_output.write_text(render_readiness_svg(result) + "\n", encoding="utf-8")
    args.report_output.write_text(
        render_readiness_markdown(result, svg_name=args.svg_output.name),
        encoding="utf-8",
    )
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["score"] == 100 else 2


if __name__ == "__main__":
    raise SystemExit(main())
