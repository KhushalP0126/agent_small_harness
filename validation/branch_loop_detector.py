from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BranchStateSignature:
    """Compact runtime state for detecting unproductive repair branch loops."""

    goal_section: str
    selected_branch_action: str
    files_artifacts_touched: tuple[str, ...]
    test_result_category: str
    failure_reason_category: str
    unresolved_requirements: tuple[str, ...]

    @property
    def cycle_key(self) -> tuple[str, str, str, str, tuple[str, ...]]:
        return (
            self.goal_section,
            self.selected_branch_action,
            self.test_result_category,
            self.failure_reason_category,
            self.unresolved_requirements,
        )

    @property
    def failure_key(self) -> tuple[str, str, tuple[str, ...]]:
        return (
            self.test_result_category,
            self.failure_reason_category,
            self.unresolved_requirements,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BranchLoopResult:
    detected: bool
    reason: str = ""
    message: str = ""
    repeated_signature: dict[str, Any] = field(default_factory=dict)
    prior_attempt: int | None = None
    current_attempt: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_branch_state_signature(target: str, attempt: Any) -> BranchStateSignature:
    validation = _mapping(_get(attempt, "validation", {}))
    behavior = _mapping(_get(attempt, "behavior_validation", {}))
    formal = _mapping(_get(attempt, "formal_validation", {}))
    static_violations = [_mapping(item) for item in validation.get("violations", [])]
    behavior_issues = [_mapping(item) for item in behavior.get("issues", [])]
    formal_issues = [_mapping(item) for item in formal.get("issues", [])]
    unresolved = _unresolved_requirements(static_violations, behavior_issues, formal_issues)
    return BranchStateSignature(
        goal_section=_goal_section(target),
        selected_branch_action=_selected_branch_action(attempt),
        files_artifacts_touched=_artifact_touch_signature(attempt),
        test_result_category=_test_result_category(validation, behavior, formal),
        failure_reason_category=_failure_reason_category(static_violations, behavior_issues, formal_issues),
        unresolved_requirements=tuple(unresolved),
    )


def detect_branching_loop(attempts: list[Any]) -> BranchLoopResult:
    if len(attempts) < 2:
        return BranchLoopResult(detected=False)
    current_index = len(attempts) - 1
    current = attempts[-1]
    current_signature = _signature(current)
    previous = attempts[-2]
    previous_signature = _signature(previous)

    if _has_progress(previous, current):
        return BranchLoopResult(detected=False)

    if _no_artifact_progress(current):
        return _detected(
            reason="no_new_artifact_progress",
            message="The repair branch did not produce a changed artifact and the same requirements remain unresolved.",
            signature=current_signature,
            prior_attempt=current_index - 1,
            current_attempt=current_index,
        )

    if current_signature.cycle_key == previous_signature.cycle_key:
        return _detected(
            reason="branching_loop_detected",
            message="The controller revisited the same branch state without reducing unresolved requirements.",
            signature=current_signature,
            prior_attempt=current_index - 1,
            current_attempt=current_index,
        )

    for prior_index, prior_attempt in enumerate(attempts[:-2]):
        prior_signature = _signature(prior_attempt)
        if current_signature.cycle_key == prior_signature.cycle_key:
            return _detected(
                reason="branching_loop_detected",
                message="The controller cycled back to an earlier branch state without artifact/test progress.",
                signature=current_signature,
                prior_attempt=prior_index,
                current_attempt=current_index,
            )

    if current_index >= 2 and current_signature.failure_key == previous_signature.failure_key:
        return _detected(
            reason="same_failure_after_branch_switch",
            message="The controller switched branch actions but reached the same unresolved failure state.",
            signature=current_signature,
            prior_attempt=current_index - 1,
            current_attempt=current_index,
        )

    return BranchLoopResult(detected=False)


def _detected(
    reason: str,
    message: str,
    signature: BranchStateSignature,
    prior_attempt: int,
    current_attempt: int,
) -> BranchLoopResult:
    return BranchLoopResult(
        detected=True,
        reason=reason,
        message=message,
        repeated_signature=signature.to_dict(),
        prior_attempt=prior_attempt,
        current_attempt=current_attempt,
    )


def _signature(attempt: Any) -> BranchStateSignature:
    existing = _get(attempt, "branch_state_signature", None)
    if existing:
        return BranchStateSignature(
            goal_section=str(existing.get("goal_section", "")),
            selected_branch_action=str(existing.get("selected_branch_action", "")),
            files_artifacts_touched=tuple(existing.get("files_artifacts_touched", ())),
            test_result_category=str(existing.get("test_result_category", "")),
            failure_reason_category=str(existing.get("failure_reason_category", "")),
            unresolved_requirements=tuple(existing.get("unresolved_requirements", ())),
        )
    return build_branch_state_signature("", attempt)


def _has_progress(previous: Any, current: Any) -> bool:
    previous_score = _failure_score(previous)
    current_score = _failure_score(current)
    if current_score < previous_score:
        return True
    for delta in _get(current, "diagnostic_deltas", []) or []:
        if _mapping(delta).get("improved"):
            return True
    return False


def _failure_score(attempt: Any) -> int:
    validation = _mapping(_get(attempt, "validation", {}))
    behavior = _mapping(_get(attempt, "behavior_validation", {}))
    formal = _mapping(_get(attempt, "formal_validation", {}))
    return (
        len(validation.get("violations", []) or [])
        + len(behavior.get("issues", []) or [])
        + len(formal.get("issues", []) or [])
    )


def _no_artifact_progress(attempt: Any) -> bool:
    return not bool(_get(attempt, "changed", True)) or not str(_get(attempt, "diff", "")).strip()


def _selected_branch_action(attempt: Any) -> str:
    return str(_get(attempt, "draft_source_worker", "") or "draft_supplier")


def _artifact_touch_signature(attempt: Any) -> tuple[str, ...]:
    draft = str(_get(attempt, "draft", ""))
    changed = bool(_get(attempt, "changed", False))
    diff = str(_get(attempt, "diff", ""))
    digest = hashlib.sha1(draft.encode("utf-8")).hexdigest()[:10]
    return (
        "generated_source.py",
        f"changed={changed}",
        f"diff_chars={len(diff)}",
        f"draft_sha1={digest}",
    )


def _test_result_category(validation: dict[str, Any], behavior: dict[str, Any], formal: dict[str, Any]) -> str:
    categories: list[str] = []
    if not validation.get("is_compliant", True):
        categories.append("static_fail")
    if not behavior.get("is_compliant", True):
        categories.append("behavior_fail")
    if not formal.get("is_compliant", True):
        categories.append("formal_fail")
    return "+".join(categories) if categories else "pass"


def _failure_reason_category(
    static_violations: list[dict[str, Any]],
    behavior_issues: list[dict[str, Any]],
    formal_issues: list[dict[str, Any]],
) -> str:
    reasons: list[str] = []
    reasons.extend(str(item.get("kind", "")) for item in static_violations)
    reasons.extend(f"behavior:{item.get('case', '')}" for item in behavior_issues)
    reasons.extend(f"formal:{item.get('summary', '')}" for item in formal_issues)
    return ",".join(_sorted_nonempty(reasons)) or "none"


def _unresolved_requirements(
    static_violations: list[dict[str, Any]],
    behavior_issues: list[dict[str, Any]],
    formal_issues: list[dict[str, Any]],
) -> list[str]:
    requirements: list[str] = []
    for item in static_violations:
        kind = item.get("kind", "static")
        current = item.get("current_value", "")
        allowed = item.get("allowed_value", "")
        requirements.append(f"{kind}:{current}->{allowed}")
    for item in behavior_issues:
        requirements.append(f"behavior:{item.get('case', '')}:{item.get('actual', '')}->{item.get('expected', '')}")
    for item in formal_issues:
        requirements.append(f"formal:{item.get('summary', '')}")
    return _sorted_nonempty(requirements)


def _goal_section(target: str) -> str:
    normalized = " ".join(str(target).split())
    if not normalized:
        return ""
    return normalized[:160]


def _sorted_nonempty(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
