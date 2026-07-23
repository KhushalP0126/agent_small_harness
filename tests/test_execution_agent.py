import unittest

from agents.execution_agent import ExecutionAgent
from agents.generation_controller import GenerationController
from harness_kernel.tool_registry import ToolHandler, ToolRegistry
from validation.behavior import BehaviorCase, FunctionBehaviorSpec
from validation.debugger import build_debugger_hints


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

    def test_controller_default_on_attaches_trace(self) -> None:
        spec = _spec()
        default = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: WRONG_SOURCE,
            behavior_spec=spec,
        )
        attempt = default.run(target="add", initial_prompt="x").payload["attempts"][-1]
        self.assertTrue(attempt["execution_trace"])
        self.assertEqual(attempt["execution_trace"]["cases"][0]["returned"], "-1")
        self.assertFalse(attempt["behavior_validation"]["is_compliant"])

    def test_controller_explicit_opt_out_leaves_trace_empty(self) -> None:
        disabled = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: WRONG_SOURCE,
            behavior_spec=_spec(),
            enable_execution_trace=False,
            enable_debugger_hints=False,
        )
        attempt = disabled.run(target="add", initial_prompt="x").payload["attempts"][-1]
        self.assertEqual(attempt["execution_trace"], {})
        self.assertFalse(attempt["behavior_validation"]["is_compliant"])

    def test_controller_uses_tool_registry_when_provided(self) -> None:
        from harness_kernel.tool_handlers import build_default_tool_registry

        controller = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: WRONG_SOURCE,
            behavior_spec=_spec(),
            tool_registry=build_default_tool_registry(),
        )
        attempt = controller.run(target="add", initial_prompt="x").payload["attempts"][-1]
        self.assertTrue(attempt["execution_trace"])
        self.assertEqual(attempt["execution_trace"]["cases"][0]["returned"], "-1")

    def test_controller_falls_back_when_registry_handler_breaks(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolHandler(
                name="execution_sandbox",
                request_type=object,
                response_type=object,
                invoke=lambda _request: (_ for _ in ()).throw(
                    RuntimeError("sandbox is on fire")
                ),
            )
        )
        controller = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: WRONG_SOURCE,
            behavior_spec=_spec(),
            tool_registry=registry,
        )
        attempt = controller.run(target="add", initial_prompt="x").payload["attempts"][-1]
        self.assertEqual(attempt["execution_trace"], {})
        self.assertFalse(attempt["behavior_validation"]["is_compliant"])

    def test_controller_does_not_execute_trace_before_parse_success(self) -> None:
        controller = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: "def add(:\n",
            behavior_spec=_spec(),
            enable_execution_trace=True,
        )
        attempt = controller.run(target="add", initial_prompt="x").payload["attempts"][-1]
        self.assertEqual(attempt["execution_trace"], {})
        self.assertFalse(attempt["validation"]["is_compliant"])

    def test_debugger_hook_can_include_accepted_type_contracts(self) -> None:
        trace = ExecutionAgent().execute(WRONG_SOURCE, _spec())
        hints = build_debugger_hints(
            trace,
            type_contracts=["Calculator.add(a, b) -> int"],
        )
        self.assertTrue(any("produced -1" in hint for hint in hints))
        self.assertTrue(any("Calculator.add(a, b)" in hint for hint in hints))


if __name__ == "__main__":
    unittest.main()
