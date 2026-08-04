from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from agents.base import AgentResult, BaseAgent
from prompt.builder import build_prompt
from prompt.constraint_types import BranchConstraint, ConstraintBlock, LoopConstraint, MutationConstraint
from validation.behavior import FunctionBehaviorSpec, format_behavior_spec


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTEXT_FILES = (ROOT / "docs" / "reference" / "design.md",)


class CoderAgent(BaseAgent):
    name = "agent-1-coder"

    def build_repair_prompt(
        self,
        target_code: str,
        behavior_spec: FunctionBehaviorSpec | None = None,
        template_name: str = "",
        template_code: str = "",
        context_files: Iterable[Path] = DEFAULT_CONTEXT_FILES,
    ) -> str:
        context_sections = self._load_context_sections(context_files)
        template_sections = self._template_sections(template_name, template_code)
        return "\n".join(
            [
                "You are an autonomous repair agent.",
                "",
                "Target: Refactor the provided code to be production-ready.",
                "",
                "Compliance Requirements (Static):",
                "",
                "Eliminate all global variable mutations.",
                "Remove references to STATE or other module/global state entirely unless that state is passed in as an explicit function argument.",
                "",
                "Reduce cyclomatic complexity to < 5 by extracting helper functions.",
                "Use table-driven, arithmetic, or data-mapping logic instead of replacing one if/elif chain with another if/elif chain.",
                "",
                "Eliminate module-state mutations.",
                "",
                "Functional Requirements (Behavioral):",
                "",
                "The refactored function must maintain strict input/output parity with the original specification.",
                "",
                "If the logic involves a matrix or collection, handle edge cases (empty input, null pointers) explicitly.",
                "",
                "Do not over-optimize to the point of returning trivial or hardcoded values; the engine will validate your logic against test cases.",
                "For the matrix scoring behavior, preserve the scoring classes: negative -> 1, zero -> 2, 1..9 -> 3, 10..99 -> 4, >=100 -> 5.",
                "",
                format_behavior_spec(behavior_spec) if behavior_spec else "",
                "",
                "Do not introduce imports, unresolved type annotations, file I/O, eval, exec, or external dependencies.",
                "Do not include demo code, print statements, example invocations, or if __name__ == '__main__' blocks.",
                "",
                *template_sections,
                *context_sections,
                "Output: Provide only the refactored code. Use clear, descriptive function names.",
                "",
                "CURRENT DRAFT:",
                target_code,
            ]
        )

    def _template_sections(self, template_name: str, template_code: str) -> list[str]:
        if not template_name or not template_code:
            return []
        return [
            "Template-Directed Synthesis:",
            f"Use the pre-validated `{template_name}` skeleton below as the base structure.",
            "Preserve its structure unless a behavioral requirement forces a minimal local adjustment.",
            "Do not add global state, demo code, print calls, or extra branches around this skeleton.",
            "",
            "PRE-VALIDATED TEMPLATE:",
            template_code.strip(),
            "",
        ]

    def _load_context_sections(self, context_files: Iterable[Path]) -> list[str]:
        sections: list[str] = []
        for path in context_files:
            if not path.exists():
                continue
            sections.extend(
                [
                    f"Additional Context From {path.name}:",
                    path.read_text(encoding="utf-8").strip(),
                    "",
                ]
            )
        return sections

    def build_design_constraint_prompt(self, design_context: str) -> str:
        return "\n".join(
            [
                "### Visual & Architectural Design Constraints",
                "You are provided with a design.md context. You must adhere to the design system described within it when generating code.",
                "Instructions:",
                "1. Token Adherence: When defining styles, spacing, or component properties, you MUST use the values defined in design.md. Do not invent new magic numbers or colors.",
                "2. Component Logic: Use the component patterns defined in design.md. If a component exists in the design system, use it rather than building a custom implementation.",
                "3. Validation: Your code will be audited against the tokens in design.md. Any violation of the design system will be treated as a STATIC_VIOLATION by the harness.",
                "",
                "Design Context:",
                design_context,
                "",
                "Current Task:",
                "Refactor the target code to satisfy static engine rules, behavioral correctness, and the design constraints listed above.",
            ]
        )

    def build_feedback_context(self, failed_attempts: list[dict]) -> str:
        if not failed_attempts:
            return ""
        lines = ["Prior Failed Attempts:"]
        for attempt in failed_attempts:
            lines.append(f"- Attempt {attempt['attempt']}:")
            for violation in attempt["validation"].get("violations", []):
                lines.append(
                    f"  Static failure: {violation['kind']} had {violation['current_value']}; required {violation['allowed_value']}."
                )
            for issue in attempt["behavior_validation"].get("issues", []):
                lines.append(
                    f"  Behavior failure: {issue['case']} expected {issue['expected']} but got {issue['actual']} ({issue['details']})."
                )
        lines.append("Do not repeat these failed patterns in the next draft.")
        return "\n".join(lines)

    def run(
        self,
        gen_id: str,
        goal: str,
        lessons: list[str],
        conventions: list[str],
        dependency_context: list[str],
        loop_constraint: LoopConstraint,
        branch_constraint: BranchConstraint,
        mutation_constraint: MutationConstraint,
    ) -> AgentResult:
        block = ConstraintBlock(
            goal=goal,
            loops=loop_constraint,
            branches=branch_constraint,
            mutations=mutation_constraint,
            conventions=conventions,
            dependency_context=dependency_context,
            lessons_learned=lessons,
        )
        prompt = build_prompt(block)
        return AgentResult(
            agent=self.name,
            payload={
                "gen_id": gen_id,
                "constraint_block": asdict(block),
                "prompt": prompt,
            },
        )
