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
        self.assertNotIn("NOISE", prompt)
        self.assertNotIn("unrelated_large_context", prompt)


if __name__ == "__main__":
    unittest.main()
