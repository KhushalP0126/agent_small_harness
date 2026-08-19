"""Versioned receipts for controlled, approval-reviewed research sessions."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from harness_kernel.provenance import collect_provenance


LIVE_SESSION_SCHEMA_VERSION = 1
SCENARIOS = frozenset(
    {
        "plain_question",
        "small_edit",
        "multi_file_edit",
        "planning_review",
        "unavailable_api",
    }
)
DECISIONS = frozenset({"approved", "rejected", "not_applicable"})


def build_live_session_receipt(
    *,
    repository_root: Path,
    scenario: str,
    prompt_summary: str,
    provider: str,
    model: str,
    approvals: Iterable[str],
    validation_status: str,
    outcome: str,
    tool_calls: int,
    artifact_reference: str = "",
    proposed_diff: str = "",
) -> dict[str, Any]:
    """Create a secret-free receipt for one controlled fixture-repo session."""

    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    _safe_text("prompt summary", prompt_summary)
    _safe_text("artifact reference", artifact_reference)
    _safe_text("outcome", outcome)
    parsed_approvals = _parse_approvals(approvals)
    if scenario in {"small_edit", "multi_file_edit", "planning_review"} and not parsed_approvals:
        raise ValueError(f"{scenario} requires at least one recorded approval decision")
    if scenario == "multi_file_edit" and {item["decision"] for item in parsed_approvals} != {"approved", "rejected"}:
        raise ValueError("multi_file_edit requires both an approved and rejected proposal")
    return {
        "schema_version": LIVE_SESSION_SCHEMA_VERSION,
        "scenario": scenario,
        "prompt_summary": prompt_summary,
        "provider": provider,
        "model": model,
        "tool_calls": max(0, int(tool_calls)),
        "approvals": parsed_approvals,
        "validation_status": validation_status,
        "outcome": outcome,
        "artifact_reference": artifact_reference,
        "proposed_diff_sha256": sha256(proposed_diff.encode("utf-8")).hexdigest() if proposed_diff else "",
        "provenance": collect_provenance(repository_root=repository_root),
    }


def validate_live_session_receipts(receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate the complete, controlled five-session evidence set.

    This validates evidence that came from real reviewed sessions; it never
    manufactures receipts. It prevents a publication checklist from being
    satisfied by duplicate or incomplete scenarios.
    """

    seen: set[str] = set()
    errors: list[str] = []
    for index, receipt in enumerate(receipts, start=1):
        scenario = receipt.get("scenario")
        if not isinstance(scenario, str) or scenario not in SCENARIOS:
            errors.append(f"receipt {index}: unknown scenario")
            continue
        if scenario in seen:
            errors.append(f"receipt {index}: duplicate scenario {scenario}")
        seen.add(scenario)
        if receipt.get("schema_version") != LIVE_SESSION_SCHEMA_VERSION:
            errors.append(f"receipt {index}: unsupported schema version")
        if not isinstance(receipt.get("provenance"), Mapping):
            errors.append(f"receipt {index}: missing provenance")
        approvals = receipt.get("approvals")
        if not isinstance(approvals, list):
            errors.append(f"receipt {index}: approvals must be a list")
            continue
        decisions = {
            item.get("decision")
            for item in approvals
            if isinstance(item, Mapping) and isinstance(item.get("decision"), str)
        }
        if scenario in {"small_edit", "planning_review"} and not decisions:
            errors.append(f"receipt {index}: {scenario} requires an approval decision")
        if scenario == "multi_file_edit" and decisions != {"approved", "rejected"}:
            errors.append(
                f"receipt {index}: multi_file_edit requires approved and rejected decisions"
            )

    missing = sorted(SCENARIOS - seen)
    if missing:
        errors.append(f"missing scenarios: {', '.join(missing)}")
    return {
        "complete": not errors,
        "required_scenarios": sorted(SCENARIOS),
        "recorded_scenarios": sorted(seen),
        "errors": errors,
    }


def _parse_approvals(values: Iterable[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for value in values:
        proposal, separator, decision = value.partition("=")
        if not separator or not proposal.strip() or decision not in DECISIONS:
            raise ValueError("approvals must use proposal_id=approved|rejected|not_applicable")
        parsed.append({"proposal_id": proposal.strip(), "decision": decision})
    return parsed


def _safe_text(label: str, value: str) -> None:
    lowered = value.casefold()
    if "api_key" in lowered or "authorization:" in lowered or "bearer " in lowered:
        raise ValueError(f"{label} must not contain secrets")
    if Path(value).is_absolute():
        raise ValueError(f"{label} must not contain an absolute path")
