from __future__ import annotations

from validation.types import Violation


def _failure_lines(violations: list[Violation]) -> list[str]:
    lines: list[str] = []
    for violation in violations:
        lines.extend(
            [
                f"- Kind: {violation.kind}",
                f"  Summary: {violation.summary}",
                f"  Current: {violation.current_value}",
                f"  Required: {violation.allowed_value}",
                f"  Hint: {violation.repair_hint}",
            ]
        )
    if not lines:
        return ["- No structured violations were supplied; preserve behavior and satisfy the state graph."]
    return lines


def build_state_machine_architect_prompt(
    current_code: str,
    violations: list[Violation],
    preserved_context: str,
) -> str:
    """Build an architect-tier prompt for hard stateful parser/event tasks."""

    return "\n".join(
        [
            "STATE MACHINE ARCHITECT MODE",
            "",
            "You are not doing a vague repair. You are designing a correct, low-complexity state machine.",
            "Produce complete Python code that passes behavior validation and static gates.",
            "",
            "PRESERVED TASK CONTEXT:",
            preserved_context.strip() or "(none)",
            "",
            "CURRENT FAILURES:",
            *_failure_lines(violations),
            "",
            "REQUIRED STATE-MACHINE SHAPE:",
            "- initialize result = {} as a nested dict",
            "- initialize active_section = None",
            "- process input one stripped line at a time",
            "- skip blank lines and comment lines",
            "- valid section headers update active_section",
            "- ignore empty or malformed section headers",
            "- ignore key/value records before active_section exists",
            "- accept key/value records only when the line contains exactly one equals sign",
            "- trim key and value whitespace",
            "- ignore records with empty keys",
            "- store values under result[active_section][key]",
            "- later valid records overwrite earlier records",
            "- return the nested result dict",
            "",
            "STATE PROPAGATION RULE:",
            "- If you use a helper that changes parser state, return the updated state and assign it at the call site.",
            "- Do not update section/current state only inside a helper without returning it.",
            "- Prefer keeping active_section updates in the main loop if that is simpler.",
            "",
            "COMPLEXITY RULE:",
            "- Keep cyclomatic complexity <= 7.",
            "- Prefer guard clauses and early continues.",
            "- Do not replace one large if/elif chain with another large if/elif chain.",
            "",
            "CURRENT DRAFT:",
            current_code,
            "",
            "Return only complete Python code.",
        ]
    )
