import unittest

from prompt.retry_builder import build_small_worker_retry_prompt
from validation.types import Violation


class ContextWindowPromptTests(unittest.TestCase):
    def test_cleaned_context_retry_prompt_keeps_refactor_signal_focused(self) -> None:
        source = """NOISE = 'large unrelated instruction blob'


def helper_one(value):
    return value + 1


def classify_status(record):
    status = record.get('status')
    kind = record.get('kind')
    priority = record.get('priority')
    if status == 'new' and kind == 'bug' and priority == 'high':
        return 'triage-now'
    if status == 'new' and kind == 'bug':
        return 'triage'
    if status == 'new' and kind == 'feature':
        return 'plan'
    if status == 'paused':
        return 'paused'
    if status == 'done':
        return 'done'
    return 'unknown'


def unrelated_large_context():
    return ['noise'] * 100
"""
        violation = Violation(
            kind="cyclomatic_complexity",
            engine="engine-3-branching",
            severity="High",
            summary="Cyclomatic complexity 9 with 8 conditional branches",
            rationale="Decision density estimates how many independent paths the code exposes.",
            current_value="9",
            allowed_value="<= 5",
            repair_hint="split_function",
            evidence={
                "function_name": "classify_status",
                "unit_test": "classify_status({'status': 'paused'}) should return 'paused'",
            },
        )

        prompt = build_small_worker_retry_prompt(source, [violation])

        self.assertIn("classify_status", prompt)
        self.assertIn("<= 5", prompt)
        self.assertIn("should return 'paused'", prompt)
        self.assertIn("NOISE", prompt)
        self.assertIn("unrelated_large_context", prompt)

    def test_negative_range_strings_do_not_trigger_signed_token_hint(self) -> None:
        source = """
def compact_ranges(values):
    return [str(value) for value in values]
"""
        violation = Violation(
            kind="behavior_mismatch",
            engine="behavior-validator",
            severity="High",
            summary="Failed behavioral output spec",
            rationale="Return value did not match the behavior spec.",
            current_value="negative returned ['-3', '-1']",
            allowed_value="['-3--1']",
            repair_hint="preserve_behavior",
            evidence={"case": {"expected": "['-3--1']", "actual": "['-3', '-1']"}},
        )

        prompt = build_small_worker_retry_prompt(source, [violation])

        self.assertIn("Change the logic so the failed output exactly matches", prompt)
        self.assertNotIn("signed integer tokens", prompt)
        self.assertNotIn("str.isdigit", prompt)

    def test_state_flow_retry_prompt_names_return_updated_state_fix(self) -> None:
        source = """
def process_line(line, section):
    if line.startswith("["):
        section = line.strip("[]")
    return None

def parse_sectioned_config(text):
    active_section = None
    for line in text.splitlines():
        process_line(line, active_section)
    return {}
"""
        violation = Violation(
            kind="state_flow_risk",
            engine="engine-7-state-flow",
            severity="Medium",
            summary="Potential lost state update",
            rationale="A helper assigns to a state-like parameter but does not return the updated state.",
            current_value="section",
            allowed_value="helper returns updated state and caller assigns it",
            repair_hint="return_updated_state",
        )

        prompt = build_small_worker_retry_prompt(source, [violation])

        self.assertIn("return the updated state", prompt.lower())
        self.assertIn("assign it at the call site", prompt.lower())


if __name__ == "__main__":
    unittest.main()
