from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.repair_templates import detect_scoring_matrix_pattern
from validation.behavior import (
    BehaviorCase,
    BehaviorResult,
    FunctionBehaviorSpec,
    format_behavior_spec,
    mixed_hard_case_spec,
    validate_function_behavior,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BEHAVIOR_CASES = ROOT / "data" / "behavior_cases.json"


class BehaviorSpecAgent:
    """Resolves and validates behavior specs.

    Replaces hardcoded ``mixed_hard_case_spec()`` call sites with pattern-driven
    resolution, JSON-backed loading, prompt formatting, and validation, so behavior
    cases scale beyond a single fixture.
    """

    name = "agent-behavior-spec"

    def for_source(self, source: str) -> FunctionBehaviorSpec | None:
        """Map a detected code pattern to a behavior spec, or None if unknown."""
        if detect_scoring_matrix_pattern(source):
            return mixed_hard_case_spec()
        return None

    def resolve(
        self,
        source: str,
        spec_file: Path | None = None,
        spec_name: str | None = None,
        fallback: FunctionBehaviorSpec | None = None,
    ) -> FunctionBehaviorSpec | None:
        """Best-effort resolution: explicit file first, then pattern, then fallback."""
        if spec_file is not None and spec_name:
            return self.load_from_file(spec_file, spec_name)
        detected = self.for_source(source)
        if detected is not None:
            return detected
        return fallback

    def load_from_file(
        self,
        path: Path | str = DEFAULT_BEHAVIOR_CASES,
        spec_name: str = "scoring_matrix",
    ) -> FunctionBehaviorSpec:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        specs = data["specs"] if "specs" in data else data
        if spec_name not in specs:
            raise KeyError(f"Behavior spec '{spec_name}' not found in {path}.")
        raw = specs[spec_name]
        cases = [self._build_case(case) for case in raw["cases"]]
        return FunctionBehaviorSpec(function_name=raw["function_name"], cases=cases)

    def available_specs(self, path: Path | str = DEFAULT_BEHAVIOR_CASES) -> list[str]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        specs = data["specs"] if "specs" in data else data
        return sorted(specs)

    def format(self, spec: FunctionBehaviorSpec) -> str:
        return format_behavior_spec(spec)

    def validate(self, source: str, spec: FunctionBehaviorSpec) -> BehaviorResult:
        return validate_function_behavior(source, spec)

    @staticmethod
    def _build_case(case: dict[str, Any]) -> BehaviorCase:
        return BehaviorCase(
            name=case["name"],
            args=tuple(case.get("args", [])),
            expected=case["expected"],
            kwargs=dict(case.get("kwargs", {})),
        )
