from __future__ import annotations

from typing import Callable

from engines.base import BaseEngine, EngineFinding
from engines.branching_engine import BranchingEngine
from engines.hazards_engine import HazardsEngine
from engines.math_engine import MathEngine


EngineFactory = Callable[[], BaseEngine]
PARSE_CONTRACT_ENGINE = "engine-parse-contract"


def python_engine_factories() -> list[EngineFactory]:
    return [MathEngine, HazardsEngine, BranchingEngine]


class EngineRegistry:
    """Routes a draft to the correct engine set by language.

    The controller no longer knows about ``MathEngine``/``HazardsEngine``/
    ``BranchingEngine`` directly. New languages (e.g. C) are unlocked by registering
    their engine factories here without touching the controller.
    """

    def __init__(self) -> None:
        self._factories: dict[str, list[EngineFactory]] = {}

    @classmethod
    def default(cls) -> "EngineRegistry":
        registry = cls()
        registry.register("python", python_engine_factories())
        # C/C++ are registered only when tree-sitter and its grammars are importable.
        # Otherwise they stay unregistered and the parse contract gates them.
        try:
            from engines import treesitter_support

            if treesitter_support.is_available():
                from engines.treesitter_engine import treesitter_engine_factories

                for language in ("c", "cpp"):
                    registry.register(language, treesitter_engine_factories(language))
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
        for engine in self.engines_for(language):
            try:
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
