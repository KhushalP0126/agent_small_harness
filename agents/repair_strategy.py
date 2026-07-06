from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from agents.repair_templates import get_repair_template, select_repair_template
from agents.template_loader import TemplateLibrary


MODEL_ONLY = "model_only"
TEMPLATE_DIRECTED = "template_directed"
MANUAL_REVIEW = "manual_review"
REPAIR_MODES = (MODEL_ONLY, TEMPLATE_DIRECTED, MANUAL_REVIEW)


@dataclass
class RepairDecision:
    mode: str
    template_name: str = ""
    template_code: str = ""
    rationale: str = ""
    repair_instructions: list[str] = field(default_factory=list)


def _violation_kinds(violations: Iterable[Any]) -> set[str]:
    kinds: set[str] = set()
    for violation in violations or []:
        if isinstance(violation, dict):
            kind = violation.get("kind", "")
        else:
            kind = getattr(violation, "kind", "")
        if kind:
            kinds.add(kind)
    return kinds


def _behavior_issue_count(behavior_issues: Iterable[Any]) -> int:
    return sum(1 for _issue in behavior_issues or [])


def _diagnostic_refactor(violation: Any) -> str:
    evidence = violation.get("evidence", {}) if isinstance(violation, dict) else getattr(violation, "evidence", {})
    diagnostic = evidence.get("diagnostic", {}) if isinstance(evidence, dict) else {}
    return diagnostic.get("recommended_refactor", "") if isinstance(diagnostic, dict) else ""


class RepairStrategyAgent:
    """Decides how to repair a draft.

    This is the self-correction layer: it inspects the static violations and behavior
    issues, then chooses between letting the model repair freely (``model_only``),
    steering it with a pre-validated template (``template_directed``), or bailing out
    to ``manual_review`` when there is no actionable path.
    """

    name = "agent-repair-strategy"

    def select_initial_template(self, source: str, forced_template: str = "") -> tuple[str, str]:
        template_name = select_repair_template(source, forced_template=forced_template or None)
        template_code = get_repair_template(template_name) if template_name else ""
        return template_name, template_code

    def select_skeleton(
        self,
        task: str,
        language: str,
        library: TemplateLibrary | None = None,
    ) -> str:
        """Return a language-specific skeletal seed for a task, or "" if none exists."""
        library = library or TemplateLibrary()
        return library.load(task, language) or ""

    def repair_instructions_for(
        self,
        violations: Iterable[Any] | None = None,
        behavior_issues: Iterable[Any] | None = None,
        language: str = "python",
    ) -> list[str]:
        """Translate policy failures into concrete, task-agnostic repair instructions."""
        kinds = _violation_kinds(violations or [])
        instructions: list[str] = []
        for violation in violations or []:
            refactor = _diagnostic_refactor(violation)
            if refactor:
                instructions.append(f"REPAIR_INSTRUCTION: {refactor}")
        if "parse_error" in kinds:
            instructions.append(
                f"REPAIR_INSTRUCTION: Return syntactically valid {language} source only. Remove markdown fences, prose, partial code, and unresolved placeholders."
            )
        if "cyclomatic_complexity" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Reduce cyclomatic complexity by extracting small single-purpose helper functions. Prefer dictionaries, lookup tables, guard clauses, and simple data mappings over long if/elif chains."
            )
        if "loop_depth" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Reduce nested loop depth. Move inner-loop decisions into helper functions, use generator expressions where behavior stays clear, or precompute simple lookup structures."
            )
        if "global_mutation" in kinds or "module_state_mutation" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Remove global and module-state mutation. Pass state through function arguments and return updated state explicitly; do not use global statements or mutate module-level containers."
            )
        if "external_dependency" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Remove non-standard-library imports. Reimplement the required behavior with Python standard-library modules only."
            )
        if "unknown_api" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Use only APIs listed in the registered library schema. Replace invented or misplaced library calls with the documented namespace path supplied by the engine."
            )
        if "algorithmic_cost" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Remove repeated linear membership checks inside loops. Precompute a set or dictionary lookup before the loop and test against that constant-time structure."
            )
        if "lint_error" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Fix blocking lint errors. Resolve undefined names, invalid imports, impossible attribute access, bad call signatures, and fatal syntax/module errors without adding external dependencies."
            )
        if _behavior_issue_count(behavior_issues or []):
            instructions.append(
                "REPAIR_INSTRUCTION: Preserve behavioral parity. Use the failing input/output cases as tests and do not replace logic with constants or hardcoded shortcuts."
            )
        return instructions

    def decide(
        self,
        source: str,
        violations: Iterable[Any] | None = None,
        behavior_issues: Iterable[Any] | None = None,
        attempt_index: int = 0,
        max_retries: int = 0,
    ) -> RepairDecision:
        violations = list(violations or [])
        behavior_issues = list(behavior_issues or [])
        kinds = _violation_kinds(violations)

        # A draft that does not even parse must first be returned as valid code; a
        # template cannot meaningfully patch unparseable text.
        if "parse_error" in kinds:
            return RepairDecision(
                mode=MODEL_ONLY,
                rationale="Draft failed to parse; request valid code before structural repair.",
                repair_instructions=self.repair_instructions_for(violations, behavior_issues),
            )

        has_issues = bool(violations or behavior_issues)
        template_name, template_code = self.select_initial_template(source)

        if template_name and template_code and has_issues:
            return RepairDecision(
                mode=TEMPLATE_DIRECTED,
                template_name=template_name,
                template_code=template_code,
                rationale=(
                    f"Detected '{template_name}' pattern; steer the model with the "
                    "pre-validated template skeleton."
                ),
                repair_instructions=self.repair_instructions_for(violations, behavior_issues),
            )

        if has_issues:
            return RepairDecision(
                mode=MODEL_ONLY,
                rationale="Actionable violations remain; iterate with engine feedback.",
                repair_instructions=self.repair_instructions_for(violations, behavior_issues),
            )

        return RepairDecision(
            mode=MANUAL_REVIEW,
            rationale="No actionable repair path detected for the reported findings.",
        )
