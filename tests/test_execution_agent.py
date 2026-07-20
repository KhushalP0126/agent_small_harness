import unittest

from agents.execution_agent import ExecutionAgent
from agents.generation_controller import GenerationController
from validation.behavior import BehaviorCase, FunctionBehaviorSpec


PASS_SOURCE = "def add(a, b):\n    return a + b\n"
WRONG_SOURCE = "def add(a, b):\n    return a - b\n"
RAISES_SOURCE = "def add(a, b):\n    return a // 0\n"


def _spec() -> FunctionBehaviorSpec:
    return FunctionBehaviorSpec(
        function_name="add",
        cases=[BehaviorCase(name="basic", args=(2, 3), expected=5)],
    )


class ExecutionAgentTests(unittest.TestCase):
    def test_trace_records_matching_return(self) -> None:
        trace = ExecutionAgent().execute(PASS_SOURCE, _spec())
        self.assertTrue(trace.loaded)
        case = trace.cases[0]
        self.assertTrue(case.matched)
        self.assertEqual(case.returned, "5")
        self.assertEqual(case.exception_type, "")
        self.assertIsInstance(case.stdout, str)
        self.assertIsInstance(case.stderr, str)

    def test_trace_records_wrong_return(self) -> None:
        trace = ExecutionAgent().execute(WRONG_SOURCE, _spec())
        case = trace.cases[0]
        self.assertFalse(case.matched)
        self.assertEqual(case.returned, "-1")
        self.assertEqual(case.expected, "5")

    def test_trace_records_exception(self) -> None:
        trace = ExecutionAgent().execute(RAISES_SOURCE, _spec())
        case = trace.cases[0]
        self.assertEqual(case.exception_type, "ZeroDivisionError")
        self.assertTrue(case.traceback)

    def test_run_payload_summarizes_failures(self) -> None:
        result = ExecutionAgent().run(WRONG_SOURCE, _spec())
        self.assertTrue(result.payload["loaded"])
        self.assertEqual(result.payload["case_count"], 1)
        self.assertEqual(result.payload["failed_cases"], ["basic"])
        self.assertIn("cases", result.payload["trace"])

    def test_controller_attaches_trace_only_when_enabled(self) -> None:
        spec = _spec()
        enabled = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: WRONG_SOURCE,
            behavior_spec=spec,
            enable_execution_trace=True,
        )
        attempt = enabled.run(target="add", initial_prompt="x").payload["attempts"][-1]
        self.assertTrue(attempt["execution_trace"])
        self.assertEqual(attempt["execution_trace"]["cases"][0]["returned"], "-1")
        self.assertFalse(attempt["behavior_validation"]["is_compliant"])

    def test_controller_default_off_leaves_trace_empty(self) -> None:
        spec = _spec()
        default = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: WRONG_SOURCE,
            behavior_spec=spec,
        )
        attempt = default.run(target="add", initial_prompt="x").payload["attempts"][-1]
        self.assertEqual(attempt["execution_trace"], {})
        # Behavior gate still runs via the legacy path.
        self.assertFalse(attempt["behavior_validation"]["is_compliant"])


if __name__ == "__main__":
    unittest.main()
