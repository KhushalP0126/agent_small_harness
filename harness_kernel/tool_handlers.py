"""Concrete typed wrappers around existing lint and execution behavior."""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.execution_agent import ExecutionAgent
from engines.base import EngineFinding
from engines.lint_engine import LintEngine
from harness_kernel.tool_registry import ToolHandler, ToolRegistry
from validation.behavior import (
    DEFAULT_BEHAVIOR_TIMEOUT_SECONDS,
    ExecutionTrace,
    FunctionBehaviorSpec,
)


@dataclass(frozen=True)
class LintRequest:
    source: str


@dataclass(frozen=True)
class LintResult:
    findings: list[EngineFinding] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(
            finding.severity in {"High", "Fatal"} for finding in self.findings
        )


@dataclass(frozen=True)
class ExecutionRequest:
    source: str
    spec: FunctionBehaviorSpec
    timeout_seconds: float | None = None


def _make_lint_handler(
    engine: LintEngine | None = None,
) -> ToolHandler[LintRequest, LintResult]:
    engine = engine or LintEngine()

    def invoke(request: LintRequest) -> LintResult:
        return LintResult(findings=engine.scan(request.source))

    return ToolHandler(
        name="lint",
        request_type=LintRequest,
        response_type=LintResult,
        invoke=invoke,
        description="Run pylint against a source string.",
    )


def _make_execution_sandbox_handler(
    agent: ExecutionAgent | None = None,
) -> ToolHandler[ExecutionRequest, ExecutionTrace]:
    agent = agent or ExecutionAgent(
        timeout_seconds=DEFAULT_BEHAVIOR_TIMEOUT_SECONDS
    )

    def invoke(request: ExecutionRequest) -> ExecutionTrace:
        return agent.execute(
            request.source,
            request.spec,
            timeout_seconds=request.timeout_seconds,
        )

    return ToolHandler(
        name="execution_sandbox",
        request_type=ExecutionRequest,
        response_type=ExecutionTrace,
        invoke=invoke,
        description="Execute behavior examples in the isolated draft sandbox.",
    )


def build_default_tool_registry(
    lint_engine: LintEngine | None = None,
    execution_agent: ExecutionAgent | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_make_lint_handler(lint_engine))
    registry.register(_make_execution_sandbox_handler(execution_agent))
    return registry
