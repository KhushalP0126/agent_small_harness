from __future__ import annotations

from dataclasses import asdict

from engines.base import EngineFinding
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
    "demote_behavior_verified_structural_findings": False,
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


def validate_findings(
    findings: list[EngineFinding],
    policy: dict | None = None,
    behavior_verified: bool = False,
) -> ValidationResult:
    policy = {**DEFAULT_POLICY, **(policy or {})}
    violations: list[Violation] = []

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
            elif finding.summary == "Unsafe API usage" and not policy["allow_unsafe_calls"]:
                violations.append(
                    Violation(
                        kind="unsafe_call",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=", ".join(metrics.get("unsafe_calls", [])) or "unsafe call",
                        allowed_value="no unsafe API calls",
                        repair_hint="remove_unsafe_call",
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
            if finding.summary in {"Pylint error", "Pylint fatal"} and not policy["allow_lint_errors"]:
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

    return ValidationResult(is_compliant=not violations, violations=violations)


def serialize_validation_result(result: ValidationResult) -> dict:
    return {
        "is_compliant": result.is_compliant,
        "violations": [asdict(violation) for violation in result.violations],
    }
