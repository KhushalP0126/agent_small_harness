from __future__ import annotations

from agents.base import AgentResult, BaseAgent
from validation.behavior import (
    DEFAULT_BEHAVIOR_TIMEOUT_SECONDS,
    ExecutionTrace,
    FunctionBehaviorSpec,
    execute_behavior_trace,
    serialize_execution_trace,
)
from validation.debugger import minimal_failing_reproducer


class ExecutionAgent(BaseAgent):
    """Run a parsed draft against its behavior examples and capture a trace.

    This is the "after the contract parses, actually run it" step. It does not
    gate structurally on its own; it produces real runtime evidence
    (return values, stdout/stderr, exceptions) that the behavior validator
    consumes and that the debugger hook can diff against the spec sheet.
    """

    name = "agent-execution"

    def __init__(self, timeout_seconds: float = DEFAULT_BEHAVIOR_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    def execute(
        self,
        source: str,
        spec: FunctionBehaviorSpec,
        timeout_seconds: float | None = None,
    ) -> ExecutionTrace:
        return execute_behavior_trace(
            source,
            spec,
            timeout_seconds=self.timeout_seconds if timeout_seconds is None else timeout_seconds,
        )

    def run(
        self,
        source: str,
        spec: FunctionBehaviorSpec,
        timeout_seconds: float | None = None,
    ) -> AgentResult:
        trace = self.execute(source, spec, timeout_seconds=timeout_seconds)
        return AgentResult(
            agent=self.name,
            payload={
                "trace": serialize_execution_trace(trace),
                "loaded": trace.loaded,
                "case_count": len(trace.cases),
                "failed_cases": [
                    case.name
                    for case in trace.cases
                    if case.exception_type or not case.matched
                ],
                "minimal_reproducer": minimal_failing_reproducer(trace),
            },
        )
