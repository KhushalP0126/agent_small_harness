import unittest

from agents.generation_controller import GenerationController
from agents.plan_mode import PlanModeAgent
from prompt.architect_builder import build_state_machine_architect_prompt
from validation.types import Violation


class ArchitectStateMachinePromptTests(unittest.TestCase):
    def test_architect_prompt_for_stateful_parser_preserves_graph_and_skeleton(self) -> None:
        violation = Violation(
            kind="state_flow_risk",
            engine="engine-7-state-flow",
            severity="Medium",
            summary="Potential lost state update",
            rationale="A helper assigns to a state-like parameter but does not return it.",
            current_value="section",
            allowed_value="helper returns updated state and caller assigns it",
            repair_hint="return_updated_state",
        )
        preserved_context = """
EXAMPLES:
- parse_sectioned_config("[main]\\na=1") == {"main": {"a": "1"}}
STATE RULES:
- track parser state explicitly with an active section variable initialized to None
- only activate section state after a valid non-empty section header is found
- ignore key/value records until an active section exists
DEPENDENCY GRAPH:
- lines -> section state -> nested dict writes
"""

        prompt = build_state_machine_architect_prompt(
            current_code="def parse_sectioned_config(text):\n    return {}",
            violations=[violation],
            preserved_context=preserved_context,
        )

        self.assertIn("STATE MACHINE ARCHITECT MODE", prompt)
        self.assertIn("STATE RULES:", prompt)
        self.assertIn("DEPENDENCY GRAPH:", prompt)
        self.assertIn("active_section = None", prompt)
        self.assertIn("return the updated state", prompt)
        self.assertIn("exactly one equals sign", prompt)
        self.assertIn("later valid records overwrite earlier records", prompt)
        self.assertIn("CURRENT DRAFT:", prompt)

    def test_generation_controller_routes_stateful_parser_failure_to_architect_prompt(self) -> None:
        task_prompt = """
Write a Python function named parse_sectioned_config(text).
Blank lines and lines starting with # are ignored.
A section header is [section].
Key value records inside a section use key=value and require exactly one equals sign.
Ignore records before any section or with empty keys.
Later valid records overwrite earlier values in the same section.
parse_sectioned_config("[main]\\na=1") == {"main": {"a": "1"}}
"""
        plan = PlanModeAgent().plan(task_prompt)
        initial_prompt = PlanModeAgent().to_worker_packet(plan)
        source = """
def process_line(line, section):
    if line.startswith("["):
        section = line.strip("[]")
    return None

def parse_sectioned_config(text):
    result = {}
    active_section = None
    for line in text.splitlines():
        process_line(line.strip(), active_section)
    return result
"""
        captured_prompts: list[str] = []

        def architect_supplier(draft: str, retry_prompt: str) -> str:
            captured_prompts.append(retry_prompt)
            return draft

        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: source,
            repair_supplier=lambda draft, _prompt: draft,
            architect_supplier=architect_supplier,
            architect_after_repair_attempts=0,
        )
        controller.run(target="stateful parser", initial_prompt=initial_prompt)

        self.assertEqual(len(captured_prompts), 1)
        retry_prompt = captured_prompts[0]
        self.assertIn("STATE MACHINE ARCHITECT MODE", retry_prompt)
        self.assertIn("STATE RULES:", retry_prompt)
        self.assertIn("DEPENDENCY GRAPH:", retry_prompt)
        self.assertIn("active_section = None", retry_prompt)
        self.assertIn("return the updated state", retry_prompt)
        self.assertIn("exactly one equals sign", retry_prompt)


if __name__ == "__main__":
    unittest.main()
