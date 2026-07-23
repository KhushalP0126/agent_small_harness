import unittest
from dataclasses import dataclass

from harness_kernel.tool_handlers import (
    ExecutionRequest,
    LintRequest,
    LintResult,
    build_default_tool_registry,
)
from harness_kernel.tool_registry import ToolError, ToolHandler, ToolRegistry
from validation.behavior import BehaviorCase, FunctionBehaviorSpec


@dataclass(frozen=True)
class EchoRequest:
    value: int


@dataclass(frozen=True)
class EchoResponse:
    value: int


class ToolRegistryTests(unittest.TestCase):
    def test_dispatch_success(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolHandler(
                name="echo",
                request_type=EchoRequest,
                response_type=EchoResponse,
                invoke=lambda request: EchoResponse(request.value + 1),
            )
        )
        result = registry.dispatch("echo", EchoRequest(1))
        self.assertTrue(result.ok)
        self.assertEqual(result.value, EchoResponse(2))

    def test_unknown_tool_and_wrong_request_are_typed_failures(self) -> None:
        registry = ToolRegistry()
        self.assertEqual(
            registry.dispatch("missing", EchoRequest(1)).error_kind,
            "unknown_tool",
        )
        registry.register(
            ToolHandler("echo", EchoRequest, EchoResponse, lambda request: EchoResponse(request.value))
        )
        self.assertEqual(
            registry.dispatch("echo", "wrong").error_kind,
            "invalid_request_type",
        )

    def test_handler_failures_do_not_escape_dispatch(self) -> None:
        registry = ToolRegistry()

        def fail(_request: EchoRequest) -> EchoResponse:
            raise RuntimeError("kaboom")

        registry.register(ToolHandler("echo", EchoRequest, EchoResponse, fail))
        result = registry.dispatch("echo", EchoRequest(1))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "handler_exception")
        self.assertIn("kaboom", result.error)

    def test_typed_error_preserves_kind(self) -> None:
        registry = ToolRegistry()

        def fail(_request: EchoRequest) -> EchoResponse:
            raise ToolError("bad input", kind="validation_failed")

        registry.register(ToolHandler("echo", EchoRequest, EchoResponse, fail))
        self.assertEqual(
            registry.dispatch("echo", EchoRequest(1)).error_kind,
            "validation_failed",
        )

    def test_duplicate_registration_is_rejected(self) -> None:
        registry = ToolRegistry()
        handler = ToolHandler(
            "echo", EchoRequest, EchoResponse, lambda request: EchoResponse(request.value)
        )
        registry.register(handler)
        with self.assertRaises(ValueError):
            registry.register(handler)

    def test_default_handlers_dispatch(self) -> None:
        registry = build_default_tool_registry()
        lint_result = registry.dispatch("lint", LintRequest("x = 1\n"))
        self.assertTrue(lint_result.ok)
        self.assertIsInstance(lint_result.value, LintResult)

        spec = FunctionBehaviorSpec(
            function_name="add",
            cases=[BehaviorCase(name="basic", args=(2, 3), kwargs={}, expected=5)],
        )
        execution = registry.dispatch(
            "execution_sandbox",
            ExecutionRequest("def add(a, b):\n    return a + b\n", spec),
        )
        self.assertTrue(execution.ok)
        self.assertTrue(execution.value.cases[0].matched)


if __name__ == "__main__":
    unittest.main()
