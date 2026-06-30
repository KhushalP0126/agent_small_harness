from __future__ import annotations

from dataclasses import dataclass
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
            )

        if has_issues:
            return RepairDecision(
                mode=MODEL_ONLY,
                rationale="Actionable violations remain; iterate with engine feedback.",
            )

        return RepairDecision(
            mode=MANUAL_REVIEW,
            rationale="No actionable repair path detected for the reported findings.",
        )
