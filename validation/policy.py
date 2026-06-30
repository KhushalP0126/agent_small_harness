from __future__ import annotations

from dataclasses import asdict

from engines.base import EngineFinding
from validation.types import ValidationResult, Violation

DEFAULT_POLICY = {
    "max_loop_depth": 2,
    "max_cyclomatic_complexity": 7,
    "allow_explicit_globals": False,
    "allow_module_state_mutation": False,
    "allow_unsafe_calls": False,
}


def validate_findings(
    findings: list[EngineFinding],
    policy: dict | None = None,
) -> ValidationResult:
    policy = {**DEFAULT_POLICY, **(policy or {})}
    violations: list[Violation] = []

    for finding in findings:
        metrics = finding.metrics
        if finding.engine == "engine-1-math":
            max_depth = metrics.get("max_loop_depth", 0)
            if max_depth > policy["max_loop_depth"]:
                violations.append(
                    Violation(
                        kind="loop_depth",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=str(max_depth),
                        allowed_value=f"<= {policy['max_loop_depth']}",
                        repair_hint="reduce_nesting",
                        evidence={"metrics": metrics},
                    )
                )
        elif finding.engine == "engine-3-branching":
            complexity = metrics.get("cyclomatic_complexity", 0)
            if complexity > policy["max_cyclomatic_complexity"]:
                violations.append(
                    Violation(
                        kind="cyclomatic_complexity",
                        engine=finding.engine,
                        severity=finding.severity,
                        summary=finding.summary,
                        rationale=finding.details,
                        current_value=str(complexity),
                        allowed_value=f"<= {policy['max_cyclomatic_complexity']}",
                        repair_hint="split_function",
                        evidence={"metrics": metrics},
                    )
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
                        evidence={"metrics": metrics},
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
                        evidence={"metrics": metrics},
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
                        evidence={"metrics": metrics},
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
                    evidence={"metrics": metrics},
                )
            )

    return ValidationResult(is_compliant=not violations, violations=violations)


def serialize_validation_result(result: ValidationResult) -> dict:
    return {
        "is_compliant": result.is_compliant,
        "violations": [asdict(violation) for violation in result.violations],
    }
