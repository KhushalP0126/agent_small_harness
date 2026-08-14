from __future__ import annotations

from typing import Callable

from engines.base import BaseEngine, EngineFinding
from engines.bounds_engine import BoundsEngine
from engines.branching_engine import BranchingEngine
from engines.cost_engine import CostEngine
from engines.hazards_engine import HazardsEngine
from engines.lint_engine import LintEngine
from engines.math_engine import MathEngine
from engines.state_flow_engine import StateFlowEngine
from engines.decomposition_engine import DecompositionEngine, StructuralIR
from harness_kernel.tool_handlers import LintRequest, LintResult
from harness_kernel.tool_registry import ToolRegistry


EngineFactory = Callable[[], BaseEngine]
PARSE_CONTRACT_ENGINE = "engine-parse-contract"


def python_engine_factories() -> list[EngineFactory]:
    return [MathEngine, HazardsEngine, BranchingEngine, CostEngine, BoundsEngine, StateFlowEngine, LintEngine]


class EngineRegistry:
    """Routes a draft to the correct engine set by language.

    The controller no longer knows about ``MathEngine``/``HazardsEngine``/
    ``BranchingEngine`` directly. New languages (e.g. C) are unlocked by registering
    their engine factories here without touching the controller.
    """

    def __init__(self, tool_registry: ToolRegistry | None = None) -> None:
        self._factories: dict[str, list[EngineFactory]] = {}
        self.tool_registry = tool_registry

    @classmethod
    def default(cls, tool_registry: ToolRegistry | None = None) -> "EngineRegistry":
        registry = cls(tool_registry=tool_registry)
        registry.register("python", python_engine_factories())
        # Strict compilation is useful even when optional tree-sitter grammars
        # are unavailable. Structural engines are appended per language when
        # that language's grammar is present.
        from engines.compilation_engine import CompilationEngine

        for language in ("c", "cpp", "rust", "javascript"):
            registry.register(
                language,
                [lambda language=language: CompilationEngine(language)],
            )
        try:
            from engines import treesitter_support

            for language in ("c", "cpp", "rust", "javascript"):
                if not treesitter_support.is_language_available(language):
                    continue
                from engines.treesitter_engine import treesitter_engine_factories

                factories: list[EngineFactory] = []
                factories.append(lambda language=language: CompilationEngine(language))
                factories.extend(treesitter_engine_factories(language))
                registry.register(language, factories)
        except Exception:
            pass
        return registry

    def register(self, language: str, factories: list[EngineFactory]) -> None:
        self._factories[language.strip().lower()] = list(factories)

    def languages(self) -> list[str]:
        return sorted(self._factories)

    def has_language(self, language: str) -> bool:
        return language.strip().lower() in self._factories

    def engines_for(self, language: str) -> list[BaseEngine]:
        return [factory() for factory in self._factories.get(language.strip().lower(), [])]

    def findings_for(self, source: str, language: str) -> list[EngineFinding]:
        findings: list[EngineFinding] = []
        ir: StructuralIR | None = None
        try:
            if language.strip().lower() == "python":
                ir = DecompositionEngine().decompose(source)
        except SyntaxError as exc:
            return [
                EngineFinding(
                    engine=PARSE_CONTRACT_ENGINE,
                    severity="High",
                    summary="Draft parse failure",
                    details=f"Generated draft is not valid Python: {exc.msg}",
                    metrics={
                        "line": exc.lineno or 0,
                        "offset": exc.offset or 0,
                        "error": exc.msg,
                    },
                )
            ]
        for engine in self.engines_for(language):
            try:
                if isinstance(engine, LintEngine) and self.tool_registry is not None:
                    result = self.tool_registry.dispatch("lint", LintRequest(source))
                    if result.ok and isinstance(result.value, LintResult):
                        findings.extend(result.value.findings)
                    else:
                        findings.append(
                            EngineFinding(
                                engine="engine-lint",
                                severity="Low",
                                summary="Lint tool dispatch failed",
                                details=result.error or "Lint tool returned no result.",
                                metrics={
                                    "lint_skipped": True,
                                    "lint_status": "tool_dispatch_failed",
                                    "error_kind": result.error_kind or "tool_error",
                                },
                            )
                        )
                    continue
                if ir is not None and isinstance(
                    engine,
                    (MathEngine, HazardsEngine, BranchingEngine, CostEngine, BoundsEngine, StateFlowEngine),
                ):
                    findings.extend(engine.scan(source, ir=ir))
                else:
                    findings.extend(engine.scan(source))
            except SyntaxError as exc:
                # Defensive: the parse-contract gate should catch this first, but if an
                # engine still trips on syntax we surface the standard parse finding
                # instead of crashing the run.
                return [
                    EngineFinding(
                        engine=PARSE_CONTRACT_ENGINE,
                        severity="High",
                        summary="Draft parse failure",
                        details=f"Generated draft is not valid Python: {exc.msg}",
                        metrics={
                            "line": exc.lineno or 0,
                            "offset": exc.offset or 0,
                            "error": exc.msg,
                        },
                    )
                ]
        return findings
