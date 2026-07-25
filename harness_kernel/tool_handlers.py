"""Concrete typed wrappers around existing lint and execution behavior."""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.execution_agent import ExecutionAgent
from backends.architect_client import ArchitectApiClient, ArchitectProfile
from backends.ollama_client import (
    DEFAULT_OLLAMA_MODEL,
    OllamaClient,
    OllamaGenerationConfig,
)
from engines.base import EngineFinding
from engines.lint_engine import LintEngine
from harness_kernel.tool_registry import ToolHandler, ToolRegistry
from validation.behavior import (
    DEFAULT_BEHAVIOR_TIMEOUT_SECONDS,
    ExecutionTrace,
    FunctionBehaviorSpec,
)
from validation.deal_contracts import (
    serialize_deal_contract_result,
    validate_deal_examples,
)
from validation.formal import serialize_formal_result, validate_with_crosshair


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


@dataclass(frozen=True)
class OllamaGenerateRequest:
    prompt: str
    model: str = DEFAULT_OLLAMA_MODEL
    config: OllamaGenerationConfig | None = None
    system: str | None = None


@dataclass(frozen=True)
class ArchitectGenerateRequest:
    prompt: str
    system: str
    profile: ArchitectProfile | None = None


@dataclass(frozen=True)
class GenerateResponse:
    text: str


@dataclass(frozen=True)
class FormalVerificationRequest:
    source: str
    crosshair_enabled: bool = False
    timeout_seconds: float = 3.0


@dataclass(frozen=True)
class FormalVerificationResponse:
    result: dict


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


def _make_ollama_generate_handler(
    client: OllamaClient | None = None,
) -> ToolHandler[OllamaGenerateRequest, GenerateResponse]:
    client = client or OllamaClient()

    def invoke(request: OllamaGenerateRequest) -> GenerateResponse:
        return GenerateResponse(
            client.generate(
                prompt=request.prompt,
                model=request.model,
                config=request.config,
                system=request.system,
            )
        )

    return ToolHandler(
        name="ollama_generate",
        request_type=OllamaGenerateRequest,
        response_type=GenerateResponse,
        invoke=invoke,
        description="Generate text through the configured local Ollama backend.",
    )


def _make_architect_generate_handler(
    client: ArchitectApiClient | None = None,
) -> ToolHandler[ArchitectGenerateRequest, GenerateResponse]:
    client = client or ArchitectApiClient()

    def invoke(request: ArchitectGenerateRequest) -> GenerateResponse:
        return GenerateResponse(
            client.generate(
                prompt=request.prompt,
                system=request.system,
                profile=request.profile,
            )
        )

    return ToolHandler(
        name="architect_generate",
        request_type=ArchitectGenerateRequest,
        response_type=GenerateResponse,
        invoke=invoke,
        description="Generate text through the configured architect backend.",
    )


def _make_formal_verification_handler(
) -> ToolHandler[FormalVerificationRequest, FormalVerificationResponse]:
    def invoke(request: FormalVerificationRequest) -> FormalVerificationResponse:
        deal_result = validate_deal_examples(
            request.source,
            timeout_seconds=request.timeout_seconds,
        )
        if not deal_result.is_compliant or not deal_result.skipped:
            result = serialize_deal_contract_result(deal_result)
            result["tool"] = "deal"
            return FormalVerificationResponse(result)
        if request.crosshair_enabled:
            return FormalVerificationResponse(
                serialize_formal_result(
                    validate_with_crosshair(
                        request.source,
                        timeout_seconds=request.timeout_seconds,
                    )
                )
            )
        return FormalVerificationResponse(
            {
                "is_compliant": True,
                "skipped": True,
                "tool": "formal",
                "issues": [],
            }
        )

    return ToolHandler(
        name="formal_verification",
        request_type=FormalVerificationRequest,
        response_type=FormalVerificationResponse,
        invoke=invoke,
        description="Run Deal examples and optional CrossHair verification.",
    )


def build_default_tool_registry(
    lint_engine: LintEngine | None = None,
    execution_agent: ExecutionAgent | None = None,
    ollama_client: OllamaClient | None = None,
    architect_client: ArchitectApiClient | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_make_lint_handler(lint_engine))
    registry.register(_make_execution_sandbox_handler(execution_agent))
    registry.register(_make_ollama_generate_handler(ollama_client))
    registry.register(_make_architect_generate_handler(architect_client))
    registry.register(_make_formal_verification_handler())
    return registry
