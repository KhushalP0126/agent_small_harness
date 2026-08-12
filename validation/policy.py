from __future__ import annotations

from dataclasses import asdict

from engines.base import EngineFinding
from engines.import_risk import ADVISORY_CATEGORIES, HARD_BLOCK_CATEGORIES
from validation.types import ValidationResult, Violation

DEFAULT_POLICY = {
    "max_loop_depth": 2,
    "max_cyclomatic_complexity": 7,
    "allow_explicit_globals": False,
    "allow_module_state_mutation": False,
    "allow_external_dependencies": False,
    "allow_unknown_registered_apis": False,
    "allow_unsafe_calls": False,
    "allow_algorithmic_hotspots": False,
    "allow_bounds_warnings": True,
    "allow_state_flow_warnings": False,
    "allow_lint_errors": False,
    "allow_lint_skips": False,
    "demote_behavior_verified_structural_findings": False,
    # Import-risk categories: hard-block never demoted by behavior_verified.
    "import_risk_hard_block_categories": sorted(HARD_BLOCK_CATEGORIES),
    "import_risk_advisory_categories": sorted(ADVISORY_CATEGORIES),
    "allow_import_risk_hard_block": False,
    "allow_import_risk_advisory_block": True,  # advisory does not fail compliance by default
}


STRUCTURAL_QUALITY_KINDS = {
    "loop_depth",
    "cyclomatic_complexity",
    "algorithmic_cost",
}


def _append_if_blocking(
    violations: list[Violation],
    violation: Violation,
    *,
    policy: dict,
    behavior_verified: bool,
) -> None:
    if (
        behavior_verified
        and policy["demote_behavior_verified_structural_findings"]
        and violation.kind in STRUCTURAL_QUALITY_KINDS
    ):
        return
    violations.append(violation)


def _evidence(finding: EngineFinding) -> dict:
    return {
        "metrics": finding.metrics,
        "diagnostic": asdict(finding.diagnostic),
    }


def _import_risk_from_finding(
    finding: EngineFinding,
    policy: dict,
) -> tuple[Violation | None, Violation | None]:
    """Return (blocking_violation | None, advisory_violation | None)."""
    metrics = finding.metrics or {}
    category = metrics.get("risk_category") or ""
    enforcement = metrics.get("enforcement") or ""
    diagnostic_code = finding.diagnostic.violation if finding.diagnostic else ""

    if not category:
        # Fallback: parse summary "Import risk (category)" / "Advisory import risk (category)"
        summary = finding.summary or ""
        if summary.startswith("Import risk ("):
            category = summary[len("Import risk (") :].rstrip(")")
            enforcement = enforcement or "hard_block"
        elif summary.startswith("Advisory import risk ("):
            category = summary[len("Advisory import risk (") :].rstrip(")")
            enforcement = enforcement or "advisory"
        elif diagnostic_code == "IMPORT_RISK_BLOCK":
            enforcement = "hard_block"
        elif diagnostic_code == "IMPORT_RISK_ADVISORY":
            enforcement = "advisory"
        elif finding.summary == "Unsafe API usage":
            # Legacy tree-sitter finding without category metrics.
            category = "unsafe_memory"
            enforcement = "hard_block"

    if not category and finding.summary != "Unsafe API usage":
        return None, None

    hard_cats = set(policy.get("import_risk_hard_block_categories", HARD_BLOCK_CATEGORIES))
    adv_cats = set(policy.get("import_risk_advisory_categories", ADVISORY_CATEGORIES))
    if not enforcement:
        if category in hard_cats:
            enforcement = "hard_block"
        elif category in adv_cats:
            enforcement = "advisory"
        else:
            enforcement = "advisory"

    symbols = metrics.get("symbols") or metrics.get("unsafe_calls") or []
    current = ", ".join(symbols) if isinstance(symbols, list) else str(symbols)
    if not current:
        current = finding.diagnostic.actual if finding.diagnostic else category

    base_kwargs = dict(
        engine=finding.engine,
        severity=finding.severity,
        summary=finding.summary,
        rationale=finding.details,
        current_value=current or category,
        location=finding.diagnostic.location if finding.diagnostic else "",
        evidence=_evidence(finding),
    )

    if enforcement == "hard_block" or category in hard_cats and enforcement != "advisory":
        if policy.get("allow_import_risk_hard_block", False) or policy.get("allow_unsafe_calls", False):
            return None, None
        return (
            Violation(
                kind="import_risk_block",
                allowed_value=f"no hard-block import risk ({category})",
                repair_hint="remove_import_risk",
                **base_kwargs,
            ),
            None,
        )

    # Advisory: never blocks by default; still recorded.
    advisory = Violation(
        kind="import_risk_advisory",
        allowed_value=f"advisory import risk tolerated ({category})",
        repair_hint="remove_import_risk",
        **base_kwargs,
    )
    if not policy.get("allow_import_risk_advisory_block", True):
        # Config inverted: treat advisory as blocking when allow_* is False.
        return advisory, None
    return None, advisory


def validate_findings(
    findings: list[EngineFinding],
    policy: dict | None = None,
    behavior_verified: bool = False,
) -> ValidationResult:
    policy = {**DEFAULT_POLICY, **(policy or {})}
    violations: list[Violation] = []
    advisories: list[Violation] = []

    for finding in findings:
        metrics = finding.metrics
        if finding.engine == "engine-1-math":
            max_depth = metrics.get("max_loop_depth", 0)
            if max_depth > policy["max_loop_depth"]:
                _append_if_blocking(
                    violations,
                    Violation(
                        kind="loop_depth",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=str(max_depth),
                        allowed_value=f"<= {policy['max_loop_depth']}",
                        repair_hint="reduce_nesting",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    ),
                    policy=policy,
                    behavior_verified=behavior_verified,
                )
        elif finding.engine == "engine-3-branching":
            complexity = metrics.get("cyclomatic_complexity", 0)
            if complexity > policy["max_cyclomatic_complexity"]:
                _append_if_blocking(
                    violations,
                    Violation(
                        kind="cyclomatic_complexity",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=str(complexity),
                        allowed_value=f"<= {policy['max_cyclomatic_complexity']}",
                        repair_hint="split_function",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    ),
                    policy=policy,
                    behavior_verified=behavior_verified,
                )
        elif finding.engine == "engine-2-hazards":
            # Category-based import risks (and legacy unsafe API).
            if (
                metrics.get("risk_category")
                or finding.diagnostic.violation in {"IMPORT_RISK_BLOCK", "IMPORT_RISK_ADVISORY"}
                or finding.summary == "Unsafe API usage"
                or finding.summary.startswith("Import risk (")
                or finding.summary.startswith("Advisory import risk (")
            ):
                blocking, advisory = _import_risk_from_finding(finding, policy)
                if blocking is not None:
                    # Never demote import hard-blocks via behavior_verified.
                    violations.append(blocking)
                if advisory is not None:
                    advisories.append(advisory)
                continue

            if finding.summary == "Global mutation hazard" and not policy["allow_explicit_globals"]:
                violations.append(
                    Violation(
                        kind="global_mutation",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=", ".join(metrics.get("global_names", [])) or "global state",
                        allowed_value="no explicit globals",
                        repair_hint="remove_global_access",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    )
                )
            elif finding.summary in {
                "Module-level container mutation hazard",
                "Module-level subscript mutation hazard",
            } and not policy["allow_module_state_mutation"]:
                violations.append(
                    Violation(
                        kind="module_state_mutation",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=", ".join(metrics.get("container_names", [])) or "module state",
                        allowed_value="no module-level container mutation",
                        repair_hint="pass_state_as_argument",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    )
                )
            elif finding.summary == "External dependency usage" and not policy["allow_external_dependencies"]:
                violations.append(
                    Violation(
                        kind="external_dependency",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=", ".join(metrics.get("imports", [])) or "external dependency",
                        allowed_value="standard library imports only",
                        repair_hint="use_standard_library",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    )
                )
            elif (
                finding.summary == "Unknown registered-library API usage"
                and not policy["allow_unknown_registered_apis"]
            ):
                violations.append(
                    Violation(
                        kind="unknown_api",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=", ".join(metrics.get("unknown_api_calls", [])) or "unknown API",
                        allowed_value="registered library schema",
                        repair_hint="use_registered_api",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    )
                )
        elif finding.engine == "engine-parse-contract":
            violations.append(
                Violation(
                    kind="parse_error",
                    engine=finding.engine,
                    severity=finding.severity,
                    summary=finding.summary,
                    rationale=finding.details,
                    current_value=f"line {metrics.get('line', 0)}",
                    allowed_value="valid Python syntax",
                    repair_hint="return_valid_python",
                    location=finding.diagnostic.location,
                    evidence=_evidence(finding),
                )
            )
        elif finding.engine == "engine-4-cost":
            if finding.summary == "Linear membership test inside loop" and not policy["allow_algorithmic_hotspots"]:
                _append_if_blocking(
                    violations,
                    Violation(
                        kind="algorithmic_cost",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=", ".join(metrics.get("containers", [])) or "linear membership hotspot",
                        allowed_value="precomputed set or constant-time lookup",
                        repair_hint="precompute_lookup",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    ),
                    policy=policy,
                    behavior_verified=behavior_verified,
                )
        elif finding.engine == "engine-5-lint":
            if metrics.get("lint_skipped") and not policy["allow_lint_skips"]:
                violations.append(
                    Violation(
                        kind="lint_skipped",
                        engine=finding.engine,
                        severity="High",
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=str(metrics.get("lint_status", "skipped")),
                        allowed_value="Pylint completed successfully",
                        repair_hint="restore_lint_validation",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    )
                )
            elif finding.summary in {"Pylint error", "Pylint fatal"} and not policy["allow_lint_errors"]:
                violations.append(
                    Violation(
                        kind="lint_error",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=metrics.get("symbol", "lint error"),
                        allowed_value="no Pylint fatal/error messages",
                        repair_hint="fix_lint_error",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    )
                )
        elif finding.engine == "engine-6-bounds":
            if finding.summary == "Potential bounds risk" and not policy["allow_bounds_warnings"]:
                violations.append(
                    Violation(
                        kind="bounds_risk",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=", ".join(metrics.get("expressions", [])) or "bounds risk",
                        allowed_value="guarded in-bounds indexing",
                        repair_hint="guard_index_access",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    )
                )
        elif finding.engine == "engine-7-state-flow":
            if finding.summary == "Potential lost state update" and not policy["allow_state_flow_warnings"]:
                violations.append(
                    Violation(
                        kind="state_flow_risk",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=", ".join(metrics.get("parameters", [])) or "state parameter",
                        allowed_value="helper returns updated state and caller assigns it",
                        repair_hint="return_updated_state",
                        location=finding.diagnostic.location,
                        evidence=_evidence(finding),
                    )
                )

    return ValidationResult(
        is_compliant=not violations,
        violations=violations,
        advisories=advisories,
    )


def serialize_validation_result(result: ValidationResult) -> dict:
    return {
        "is_compliant": result.is_compliant,
        "violations": [asdict(violation) for violation in result.violations],
        "advisories": [asdict(item) for item in getattr(result, "advisories", [])],
    }
