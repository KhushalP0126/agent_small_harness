from __future__ import annotations

from validation.types import Violation


def build_retry_prompt(original_code: str, violations: list[Violation]) -> str:
    sections = [
        "You are repairing a generated draft to satisfy structural policy constraints.",
        "",
        "VIOLATIONS:",
    ]
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
