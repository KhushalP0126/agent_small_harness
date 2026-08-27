"""Render a controlled 1.5B-versus-3B Compute Shield comparison."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def render_comparison(one_point_five: dict[str, Any], three: dict[str, Any]) -> str:
    left = _metrics(one_point_five)
    right = _metrics(three)
    lines = [
        "# Local model-size comparison: Qwen 1.5B vs 3B",
        "",
        f"> Generated: {datetime.now(timezone.utc).date().isoformat()} · Fixed ten-task Compute Shield corpus",
        "",
        "## Controlled comparison",
        "",
        "Both reports use the same versioned ten-task corpus and baseline/shielded protocol. This is a two-model observation, not a parameter-scaling law.",
        "",
        "![Local model comparison](local-model-comparison.svg)",
        "",
        "| Measure | Qwen 1.5B | Qwen 3B | 3B − 1.5B |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, key in (
        ("Baseline successes", "baseline_successes"),
        ("Shielded successes", "shielded_successes"),
        ("Baseline tokens", "baseline_tokens"),
        ("Shielded tokens", "shielded_tokens"),
        ("Baseline wall seconds", "baseline_duration_seconds"),
        ("Shielded wall seconds", "shielded_duration_seconds"),
        ("Shielded tool calls", "shielded_tool_calls"),
    ):
        lines.append(f"| {label} | {left[key]:.2f} | {right[key]:.2f} | {right[key] - left[key]:+.2f} |")
    lines.extend(["", "## Interpretation", "", _interpretation(left, right), ""])
    return "\n".join(lines)


def render_comparison_svg(one_point_five: dict[str, Any], three: dict[str, Any]) -> str:
    left = _metrics(one_point_five)
    right = _metrics(three)
    success_max = max(left["shielded_successes"], right["shielded_successes"], 1.0)
    token_max = max(left["shielded_tokens"], right["shielded_tokens"], 1.0)
    bars = []
    for index, (label, metrics, color) in enumerate(
        (("Qwen 1.5B", left, "#38bdf8"), ("Qwen 3B", right, "#a78bfa"))
    ):
        x = 160 + index * 300
        success_height = 150 * metrics["shielded_successes"] / success_max
        token_height = 150 * metrics["shielded_tokens"] / token_max
        bars.append(
            f'<rect x="{x}" y="{220-success_height:.1f}" width="72" height="{success_height:.1f}" fill="{color}"/>'
            f'<rect x="{x+100}" y="{470-token_height:.1f}" width="72" height="{token_height:.1f}" fill="{color}"/>'
            f'<text x="{x}" y="245" class="label">{label}</text>'
            f'<text x="{x}" y="495" class="label">{label}</text>'
        )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="760" height="530" viewBox="0 0 760 530">
<style>.title{{font:700 22px system-ui;fill:#e5eefb}}.label{{font:14px system-ui;fill:#cbd5e1}}</style>
<rect width="100%" height="100%" rx="18" fill="#08111f"/>
<text x="36" y="42" class="title">Shielded completions</text>
<line x1="80" y1="220" x2="700" y2="220" stroke="#475569"/>
<text x="36" y="292" class="title">Shielded model tokens</text>
<line x1="80" y1="470" x2="700" y2="470" stroke="#475569"/>
{''.join(bars)}
</svg>'''


def _metrics(payload: dict[str, Any]) -> dict[str, float]:
    report = payload.get("report")
    if not isinstance(report, dict) or int(payload.get("schema_version", 0)) < 2:
        raise ValueError("expected a schema-version-2 Compute Shield report")
    results = report.get("results", [])
    if len(results) != 10:
        raise ValueError("expected the frozen ten-task Compute Shield corpus")
    return {
        "baseline_successes": float(report["baseline_successes"]),
        "shielded_successes": float(report["shielded_successes"]),
        "baseline_tokens": float(report["baseline_tokens"]),
        "shielded_tokens": float(report["shielded_tokens"]),
        "baseline_duration_seconds": sum(float(row["baseline"].get("duration_seconds", 0.0)) for row in results),
        "shielded_duration_seconds": sum(float(row["shielded"].get("duration_seconds", 0.0)) for row in results),
        "shielded_tool_calls": sum(float(row["shielded"].get("tool_calls", 0)) for row in results),
    }


def _interpretation(left: dict[str, float], right: dict[str, float]) -> str:
    success_delta = right["shielded_successes"] - left["shielded_successes"]
    if success_delta > 0:
        return "On this fixed corpus, the 3B model achieved more shielded completions than 1.5B. That is an observed contribution difference only; repeat it before generalizing to parameter count."
    if success_delta < 0:
        return "On this fixed corpus, the 3B model achieved fewer shielded completions than 1.5B. The result does not support a monotonic parameter-size claim."
    return "On this fixed corpus, both models achieved the same shielded completion count. The result does not support a monotonic parameter-size claim."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--one-point-five", type=Path, required=True)
    parser.add_argument("--three", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        one_point_five = json.loads(args.one_point_five.read_text(encoding="utf-8"))
        three = json.loads(args.three.read_text(encoding="utf-8"))
        rendered = render_comparison(one_point_five, three)
        svg = render_comparison_svg(one_point_five, three)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    args.output.with_suffix(".svg").write_text(svg + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
