"""Render one versioned benchmark JSON artifact as a dated Markdown report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def render_report(payload: dict[str, Any], *, title: str) -> str:
    _validate(payload)
    health = payload.get("health", {"comparison_eligible": True})
    comparison_eligible = bool(health.get("comparison_eligible", True)) if isinstance(health, dict) else True
    summary = payload["comparison_summary"] if comparison_eligible and payload.get("comparison_summary") else payload["summary"]
    rows = [
        ("Successful tasks", "baseline_successes", "shielded_successes"),
        ("Model tokens", "baseline_tokens", "shielded_tokens"),
        ("Tool calls", "baseline_tool_calls", "shielded_tool_calls"),
        ("Wall-clock seconds", "baseline_duration_seconds", "shielded_duration_seconds"),
    ]
    lines = [
        f"# {title}",
        "",
        f"> Generated: {datetime.now(timezone.utc).date().isoformat()} · Source schema: {payload['schema_version']}",
        "",
        "## Reproducibility",
        "",
        f"- Commit: `{payload['provenance']['repository']['commit']}`",
        f"- Working tree dirty: `{payload['provenance']['repository']['dirty']}`",
        f"- OS: `{payload['provenance']['environment']['os']}`",
        f"- Python: `{payload['provenance']['environment']['python']}`",
        f"- Corpus: `{payload['provenance']['task_corpus']['path']}`",
        f"- Corpus SHA-256: `{payload['provenance']['task_corpus']['sha256']}`",
        f"- Repetitions: `{payload['run_count']}`",
        "",
        "## Configured variants",
        "",
        *_variant_lines(payload.get("variant_metadata", {})),
        *_scope_lines(payload.get("variant_metadata", {})),
        "",
        "## Provider health gate",
        "",
        _health_line(health),
        "",
        "## " + ("Descriptive comparison summary" if comparison_eligible else "Operational summary (comparison rejected)"),
        "",
        "| Measure | Baseline mean ± SD | Shielded mean ± SD | Difference |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, baseline_key, shielded_key in rows:
        baseline = float(summary[baseline_key]["mean"])
        shielded = float(summary[shielded_key]["mean"])
        baseline_stdev = float(summary[baseline_key].get("stdev", 0.0))
        shielded_stdev = float(summary[shielded_key].get("stdev", 0.0))
        lines.append(
            f"| {label} | {baseline:.2f} ± {baseline_stdev:.2f} | "
            f"{shielded:.2f} ± {shielded_stdev:.2f} | {shielded - baseline:+.2f} |"
        )
    token_delta = float(summary["token_delta"]["mean"])
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            _interpretation(token_delta, comparison_eligible),
            "",
            "## Failures retained",
            "",
        ]
    )
    failures = _failures(payload["runs"])
    if failures:
        lines.extend(f"- Run {run}: `{task}` ({variant}) — {error}" for run, task, variant, error in failures)
    else:
        lines.append("- No failed task outcomes were recorded.")
    lines.extend(
        [
            "",
            "## Raw evidence",
            "",
            "This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.",
            "",
        ]
    )
    return "\n".join(lines)


def _validate(payload: dict[str, Any]) -> None:
    required = {"schema_version", "provenance", "run_count", "runs", "summary"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"benchmark report missing: {', '.join(missing)}")
    if int(payload["schema_version"]) < 2:
        raise ValueError("benchmark report must use schema version 2 or newer")
    provenance = payload["provenance"]
    if not isinstance(provenance, dict) or not isinstance(provenance.get("task_corpus"), dict):
        raise ValueError("benchmark report lacks provenance/task corpus metadata")


def _variant_lines(variants: Any) -> list[str]:
    if not isinstance(variants, dict):
        return ["- Variant metadata was not supplied."]
    lines: list[str] = []
    for variant in ("baseline", "shielded"):
        entries = variants.get(variant, [])
        if not entries:
            lines.append(f"- {variant}: metadata unavailable")
            continue
        emitted: set[tuple[str, str, str, str, str]] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identity = tuple(
                str(entry.get(key, "unknown"))
                for key in ("provider", "model", "context_window", "thinking_type", "reasoning_effort")
            )
            if identity in emitted:
                continue
            emitted.add(identity)
            lines.append(
                "- "
                f"{variant}: `{entry.get('provider', 'unknown')}` / `{entry.get('model', 'unknown')}` "
                f"· context `{entry.get('context_window', 'unknown')}` "
                f"· thinking `{entry.get('thinking_type', 'unknown')}` "
                f"· reasoning `{entry.get('reasoning_effort', 'unknown')}`"
            )
    return lines or ["- Variant metadata was not supplied."]


def _scope_lines(variants: Any) -> list[str]:
    if not isinstance(variants, dict):
        return []
    scopes = {
        str(entry.get("scope"))
        for entries in variants.values()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict) and entry.get("scope")
    }
    if not scopes:
        return []
    return [f"- Scope: `{scope}`" for scope in sorted(scopes)]


def _health_line(health: Any) -> str:
    if not isinstance(health, dict):
        return "- Provider health metadata was not supplied; this legacy artifact is rendered without a gate decision."
    if health.get("comparison_eligible", False):
        return "- **Eligible:** every recorded provider response was usable; aggregate comparison is allowed."
    count = int(health.get("provider_failure_count", 0) or 0)
    reason = str(health.get("reason") or "provider failure")
    return f"- **Rejected:** {reason} (`{count}` provider failure(s)); task outcomes are retained only as operational diagnostics."


def _interpretation(token_delta: float, comparison_eligible: bool) -> str:
    if not comparison_eligible:
        return "The provider health gate rejected this comparison. These totals do not support a quality, success-rate, or token-efficiency claim."
    if token_delta > 0:
        return "The shielded loop used fewer mean model tokens in this recorded experiment; this is descriptive evidence, not a general efficiency claim."
    if token_delta < 0:
        return "The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings."
    return "The recorded mean model-token use was equal; the report makes no token-efficiency claim."


def _failures(reports: list[dict[str, Any]]) -> list[tuple[int, str, str, str]]:
    rows: list[tuple[int, str, str, str]] = []
    for index, report in enumerate(reports, start=1):
        for result in report.get("results", []):
            task = str(result.get("task", {}).get("task_id", "unknown"))
            for variant in ("baseline", "shielded"):
                outcome = result.get(variant, {})
                if not outcome.get("success", False):
                    rows.append((index, task, variant, str(outcome.get("error") or "unspecified failure")))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", default="Repeated paired coding-agent benchmark")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        parser.error("input must contain a JSON object")
    try:
        rendered = render_report(payload, title=args.title)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
