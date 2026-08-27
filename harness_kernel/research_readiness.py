"""Authoritative, evidence-backed research-readiness evaluation."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from harness_kernel.live_session import validate_live_session_receipts


READINESS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReadinessCategory:
    name: str
    passed: bool
    evidence: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


def evaluate_research_readiness(repository_root: Path) -> dict[str, Any]:
    root = repository_root.resolve()
    categories = [
        _verification_category(root),
        _benchmark_category(root, "qwen_local", "fixture-qwen-1.5b-repeated-2026-08-16.json"),
        _benchmark_category(root, "deepseek_20", "deepseek-20-health-gated-2026-08-17.json"),
        _benchmark_category(root, "deepseek_fixture", "fixture-deepseek-repeated-2026-08-16.json"),
        _live_session_category(root),
        _provenance_category(root),
        _visualization_category(root),
    ]
    passed = sum(category.passed for category in categories)
    score = round(100 * passed / len(categories))
    blockers = [blocker for category in categories for blocker in category.blockers]
    return {
        "schema_version": READINESS_SCHEMA_VERSION,
        "score": score,
        "status": "ready" if score == 100 and not blockers else "blocked",
        "categories": [asdict(category) for category in categories],
        "blockers": blockers,
    }


def render_readiness_markdown(result: dict[str, Any], *, svg_name: str) -> str:
    lines = [
        "# Research readiness",
        "",
        f"![Research readiness visualization]({svg_name})",
        "",
        f"**Authoritative result: {result['score']}% · {result['status']}**",
        "",
        "| Gate | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    for category in result["categories"]:
        lines.append(
            f"| {category['name']} | {'pass' if category['passed'] else 'blocked'} | "
            f"{'; '.join(category['evidence']) or 'none'} |"
        )
    lines.extend(["", "## Blockers", ""])
    lines.extend(f"- {blocker}" for blocker in result["blockers"])
    if not result["blockers"]:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def render_readiness_svg(result: dict[str, Any]) -> str:
    categories = result["categories"]
    width = 920
    height = 150 + 42 * len(categories)
    score = int(result["score"])
    bar_width = 620
    filled = bar_width * score / 100
    rows = []
    for index, category in enumerate(categories):
        y = 150 + index * 42
        color = "#22c55e" if category["passed"] else "#ef4444"
        marker = "PASS" if category["passed"] else "BLOCKED"
        rows.append(
            f'<circle cx="52" cy="{y}" r="8" fill="{color}"/>'
            f'<text x="74" y="{y + 6}" class="label">{_xml(category["name"])}</text>'
            f'<text x="780" y="{y + 6}" class="status" fill="{color}">{marker}</text>'
        )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<style>.title{{font:700 24px system-ui;fill:#e5eefb}}.score{{font:700 20px system-ui;fill:#e5eefb}}.label{{font:16px system-ui;fill:#cbd5e1}}.status{{font:700 13px system-ui}}</style>
<rect width="100%" height="100%" rx="18" fill="#08111f"/>
<text x="42" y="40" class="title">Research readiness</text>
<rect x="42" y="64" width="{bar_width}" height="28" rx="14" fill="#1e293b"/>
<rect x="42" y="64" width="{filled:.1f}" height="28" rx="14" fill="#38bdf8"/>
<text x="684" y="86" class="score">{score}%</text>
{''.join(rows)}
</svg>'''


def _verification_category(root: Path) -> ReadinessCategory:
    path = root / "docs/results/raw/readiness/verification.json"
    payload = _load_json(path)
    python_tests = payload.get("python_tests")
    rust_tests = payload.get("rust_tests")
    passed = (
        isinstance(python_tests, dict)
        and python_tests.get("passed") is True
        and isinstance(rust_tests, dict)
        and rust_tests.get("passed") is True
    )
    active_mermaid = _active_mermaid_paths(root)
    blockers = []
    if not passed:
        blockers.append("Full Python and Rust verification evidence is missing or failing.")
    if active_mermaid:
        blockers.append("Active Mermaid/.mmd surfaces remain: " + ", ".join(active_mermaid[:8]))
    return ReadinessCategory(
        "implementation_verification",
        passed and not active_mermaid,
        [str(path.relative_to(root))] if path.is_file() else [],
        blockers,
    )


def _benchmark_category(root: Path, name: str, filename: str) -> ReadinessCategory:
    path = root / "docs/results/raw" / filename
    payload = _load_json(path)
    health = payload.get("health")
    schema_version = _safe_int(payload.get("schema_version"))
    run_count = _safe_int(payload.get("run_count"))
    passed = (
        schema_version >= 3
        and run_count >= 3
        and isinstance(payload.get("provenance"), dict)
        and isinstance(health, dict)
        and health.get("comparison_eligible") is True
    )
    blocker = [] if passed else [f"{name} needs a schema-v3+, three-run, provenance-complete, provider-healthy artifact."]
    return ReadinessCategory(name, passed, [str(path.relative_to(root))] if path.is_file() else [], blocker)


def _live_session_category(root: Path) -> ReadinessCategory:
    receipt_dir = root / "docs/results/raw/live_sessions"
    receipts = [
        payload
        for path in sorted(receipt_dir.glob("*.json"))
        if (payload := _load_json(path))
    ]
    validation = validate_live_session_receipts(receipts)
    return ReadinessCategory(
        "controlled_live_sessions",
        bool(validation["complete"]),
        [str(path.relative_to(root)) for path in sorted(receipt_dir.glob("*.json"))],
        list(validation["errors"]),
    )


def _provenance_category(root: Path) -> ReadinessCategory:
    raw = list((root / "docs/results/raw").glob("*.json"))
    current = [path for path in raw if "2026-08-16" in path.name or "2026-08-17" in path.name]
    missing = [path.name for path in current if not isinstance(_load_json(path).get("provenance"), dict)]
    reports = [
        root / "docs/results/deepseek-20-repeated-2026-08-16.md",
        root / "docs/results/fixture-qwen-1.5b-repeated-2026-08-16.md",
    ]
    absent_reports = [path.name for path in reports if not path.is_file()]
    blockers = []
    if missing:
        blockers.append("Current raw artifacts lack provenance: " + ", ".join(missing))
    if absent_reports:
        blockers.append("Required rendered reports are missing: " + ", ".join(absent_reports))
    return ReadinessCategory(
        "provenance_and_reports",
        bool(current) and not missing and not absent_reports,
        [str(path.relative_to(root)) for path in [*current, *reports] if path.is_file()],
        blockers,
    )


def _visualization_category(root: Path) -> ReadinessCategory:
    paths = [
        root / "docs/results/research-readiness.svg",
        root / "docs/results/research-readiness.md",
    ]
    valid_svg = paths[0].is_file() and "<svg" in paths[0].read_text(encoding="utf-8")
    valid_report = paths[1].is_file() and paths[0].name in paths[1].read_text(encoding="utf-8")
    passed = valid_svg and valid_report
    return ReadinessCategory(
        "native_visualizations",
        passed,
        [str(path.relative_to(root)) for path in paths if path.is_file()],
        [] if passed else ["Generate the deterministic readiness SVG and Markdown report."],
    )


def _active_mermaid_paths(root: Path) -> list[str]:
    paths = []
    historical = root / "docs/results"
    skipped = {".git", "poc-python", "target", "__pycache__", ".pytest_cache", ".venv", "artifacts"}
    active_markers = (
        "to_mermaid(",
        "render_repo_architecture_mermaid",
        "mermaid-rs-renderer",
        "repo_map.mmd",
        "repo-map.mmd",
        "mermaid.esm.min.mjs",
        "mod mermaid_view",
    )
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in skipped]
        for filename in filenames:
            path = Path(directory) / filename
            if path == Path(__file__).resolve() or "tests" in path.parts:
                continue
            if historical in path.parents and path.suffix == ".md":
                continue
            if path.suffix == ".mmd" or "mermaid" in path.name.casefold():
                paths.append(str(path.relative_to(root)))
                continue
            if path.suffix in {".py", ".rs", ".toml", ".md"}:
                try:
                    source = path.read_text(encoding="utf-8").casefold()
                    if any(marker in source for marker in active_markers):
                        paths.append(str(path.relative_to(root)))
                except (OSError, UnicodeDecodeError):
                    pass
    return sorted(set(paths))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _xml(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
