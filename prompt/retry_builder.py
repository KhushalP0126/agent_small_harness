from __future__ import annotations

from dataclasses import asdict

from validation.finding_aggregator import RepairDirective
from validation.types import Violation


def _directive_to_dict(directive: RepairDirective | dict) -> dict:
    return directive if isinstance(directive, dict) else asdict(directive)


def _behavior_hint_text(violation: Violation) -> str:
    if violation.kind != "behavior_mismatch":
        return ""
    evidence = violation.evidence.get("case", {}) if isinstance(violation.evidence, dict) else {}
    expected = str(evidence.get("expected", violation.allowed_value))
    actual = str(evidence.get("actual", violation.current_value))
    rationale = violation.rationale.lower()
    combined = f"{expected}\n{actual}\n{violation.current_value}\n{violation.allowed_value}".lower()
    if "nameerror" in combined or "nameerror" in rationale:
        return "The draft raised NameError. Remove undefined names or replace them with available builtins/helpers."
    if any(token in combined for token in ("-1", "-2", "-3", "-4", "-5", "+1", "+2", "+3", "+4", "+5")):
        return (
            "The failed behavior includes signed integer tokens. Do not use str.isdigit() alone; "
            "handle optional leading + or - signs, or use safe int conversion in a small helper."
        )
    if "empty" in combined:
        return "The failed behavior includes an empty-input edge case. Handle empty values explicitly."
    return ""


def _semantic_repair_hints(violations: list[Violation]) -> list[str]:
    hints: list[str] = []
    for violation in violations:
        hint = _behavior_hint_text(violation)
        if hint and hint not in hints:
            hints.append(hint)
    return hints


def _primary_violation(violations: list[Violation]) -> Violation | None:
    return violations[0] if violations else None


def _low_noise_directive(violation: Violation | None) -> str:
    if violation is None:
        return "Fix the current draft while preserving its required behavior."
    semantic_hints = _semantic_repair_hints([violation])
    if semantic_hints:
        return semantic_hints[0]
    if violation.kind == "algorithmic_cost":
        return "Move repeated membership checks on list-like containers out of loops by precomputing a set or dictionary."
    if violation.kind == "cyclomatic_complexity":
        return "Reduce branching by extracting a small helper or replacing repeated conditionals with a simpler data-driven structure."
    if violation.kind in {"global_mutation", "module_state_mutation"}:
        return "Remove global or module-level state mutation. Pass state as an argument and return the updated value."
    if violation.kind == "loop_depth":
        return "Reduce nested loop depth by moving inner-loop work into a helper or flattening the traversal."
    if violation.kind == "lint_error":
        return "Fix the lint error directly, especially undefined names, invalid imports, or bad calls."
    if violation.kind == "parse_error":
        return "Return complete, valid Python code only."
    if violation.kind == "external_dependency":
        return "Remove third-party imports and use Python standard-library code only."
    if violation.kind == "unknown_api":
        diagnostic = violation.evidence.get("diagnostic", {}) if isinstance(violation.evidence, dict) else {}
        refactor = diagnostic.get("recommended_refactor", "") if isinstance(diagnostic, dict) else ""
        return refactor or "Replace the invented library call with the registered API path."
    if violation.kind == "behavior_mismatch":
        return "Change the logic so the failed output exactly matches the required output. Do not hardcode only the shown case."
    if violation.kind == "formal_counterexample":
        return "Change the implementation so every contract, assertion, or symbolic counterexample reported by the verifier is satisfied."
    return violation.rationale or violation.summary or "Fix the listed issue."


def build_small_worker_retry_prompt(original_code: str, violations: list[Violation]) -> str:
    violation = _primary_violation(violations)
    sections = [
        "CRITICAL BUG FIX REQUIRED",
        "",
        "YOUR CODE:",
        original_code,
        "",
    ]
    if violation is not None:
        sections.extend(
            [
                "FAILED CHECK:",
                f"- Problem: {violation.summary}",
                f"- Your result: {violation.current_value}",
                f"- Required result: {violation.allowed_value}",
                "",
            ]
        )
    sections.extend(
        [
            "FIX DIRECTIVE:",
            _low_noise_directive(violation),
            "",
            "FINAL RULES:",
            "- Return only complete Python code.",
            "- Preserve the public function name.",
            "- Do not add imports, file I/O, network calls, eval, exec, print calls, or demo code.",
            "- Do not use global or module-level mutable state.",
        ]
    )
    return "\n".join(sections)


def build_retry_prompt(
    original_code: str,
    violations: list[Violation],
    repair_directives: list[RepairDirective | dict] | None = None,
) -> str:
    sections = [
        "You are repairing a generated draft to satisfy structural policy constraints.",
        "",
    ]
    if repair_directives:
        sections.append("COORDINATED REPAIR PLAN:")
        for directive in repair_directives:
            item = _directive_to_dict(directive)
            sections.extend(
                [
                    f"- Function: {item.get('function_name') or '<module>'}",
                    f"  Anchor: {item.get('ast_anchor') or item.get('location') or 'module'}",
                    f"  Engines: {', '.join(item.get('engines', []))}",
                    f"  Failure kinds: {', '.join(item.get('kinds', []))}",
                    f"  Required change: {item.get('instruction', '')}",
                ]
            )
            if item.get("delta_summary"):
                sections.append(f"  Retry delta: {item['delta_summary']}")
        sections.append("")

    sections.extend(
        [
            "VIOLATIONS:",
        ]
    )
    for violation in violations:
        sections.extend(
            [
                f"- Kind: {violation.kind}",
                f"  Summary: {violation.summary}",
                f"  Rationale: {violation.rationale}",
                f"  Current value: {violation.current_value}",
                f"  Allowed value: {violation.allowed_value}",
                f"  Repair hint: {violation.repair_hint}",
            ]
        )
    semantic_hints = _semantic_repair_hints(violations)
    if semantic_hints:
        sections.extend(["", "SEMANTIC REPAIR HINTS:"])
        sections.extend(f"- {hint}" for hint in semantic_hints)
    sections.extend(
        [
            "",
            "REQUEST:",
            "Refactor only the identified violations while preserving the current functionality.",
            "Do not introduce new global mutations or deeper loop nesting.",
            "Remove references to STATE or other module/global state entirely unless that state is passed in as an explicit function argument.",
            "Use table-driven, arithmetic, or data-mapping logic instead of replacing one if/elif chain with another if/elif chain.",
            "Do not introduce imports, unresolved type annotations, file I/O, eval, exec, or external dependencies.",
            "Do not include demo code, print statements, example invocations, or if __name__ == '__main__' blocks.",
            "",
            "CURRENT DRAFT:",
            original_code,
        ]
    )
    return "\n".join(sections)
