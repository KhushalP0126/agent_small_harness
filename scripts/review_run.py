from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_ARTIFACT_ROOT = Path("artifacts/runs")


def _resolve_run(value: str, artifact_root: Path) -> Path:
    candidate = Path(value)
    if candidate.is_dir():
        return candidate
    run_dir = artifact_root / value
    if run_dir.is_dir():
        return run_dir
    matches = sorted(artifact_root.glob(f"*{value}*")) if artifact_root.is_dir() else []
    if len(matches) == 1 and matches[0].is_dir():
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"No artifact run found for {value!r}.")
    names = ", ".join(match.name for match in matches[:5])
    raise ValueError(f"Run id {value!r} is ambiguous. Matches: {names}")


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_attempt(run_dir: Path) -> int:
    attempts = []
    for path in run_dir.glob("attempt_*_validation.json"):
        try:
            attempts.append(int(path.name.split("_")[1]))
        except (IndexError, ValueError):
            continue
    return max(attempts) if attempts else 0


def render_review(run_dir: Path) -> str:
    summary = _read_json(run_dir / "session_summary.json", {})
    timeline = _read_json(run_dir / "attempt_timeline.json", [])
    attempt_index = _latest_attempt(run_dir)
    validation = _read_json(run_dir / f"attempt_{attempt_index}_validation.json", {})
    lines = [
        f"Run: {run_dir}",
        f"Target: {summary.get('target', '')}",
        f"Final status: {summary.get('final_status', '')}",
        f"Route: {summary.get('route', '')}",
        "",
        "Attempt timeline:",
    ]
    if timeline:
        for row in timeline:
            lines.append(
                "- attempt {attempt}: worker={repair_worker} static={static_compliant}"
                "({static_violations}) behavior={behavior_compliant}({behavior_issues})"
                " formal={formal_compliant}({formal_issues}) changed={changed}"
                " diff_chars={diff_chars}".format(**row)
            )
    else:
        lines.append("- no attempt_timeline.json found")

    lines.extend(["", f"Latest attempt: {attempt_index}"])
    for violation in validation.get("validation", {}).get("violations", []):
        lines.append(
            f"- static {violation.get('kind')}: {violation.get('current_value')} "
            f"allowed {violation.get('allowed_value')}"
        )
    for issue in validation.get("behavior_validation", {}).get("issues", []):
        lines.append(
            f"- behavior {issue.get('case')}: expected {issue.get('expected')} "
            f"got {issue.get('actual')} ({issue.get('details')})"
        )
    for issue in validation.get("formal_validation", {}).get("issues", []):
        lines.append(f"- formal {issue.get('summary')}: {issue.get('details')}")

    human_review = summary.get("human_review") or {}
    if human_review:
        lines.extend(
            [
                "",
                f"Human review reason: {human_review.get('reason', '')}",
                f"Suggested decision: {human_review.get('suggested_human_decision', '')}",
            ]
        )

    lines.extend(
        [
            "",
            "Files:",
            f"- latest draft: {run_dir / f'attempt_{attempt_index}.py'}",
            f"- latest validation: {run_dir / f'attempt_{attempt_index}_validation.json'}",
            f"- latest retry prompt: {run_dir / f'attempt_{attempt_index}_retry_prompt.txt'}",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a concise human-review summary for an artifact run.")
    parser.add_argument("run", help="Artifact run directory, run id, or unique run-id substring.")
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    args = parser.parse_args()
    run_dir = _resolve_run(args.run, args.artifact_root)
    print(render_review(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
