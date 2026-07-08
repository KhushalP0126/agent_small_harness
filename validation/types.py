from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ViolationKind = Literal[
    "loop_depth",
    "cyclomatic_complexity",
    "global_mutation",
    "module_state_mutation",
    "parse_error",
    "behavior_mismatch",
    "external_dependency",
    "unknown_api",
    "unsafe_call",
    "algorithmic_cost",
    "lint_error",
    "formal_counterexample",
    "bounds_risk",
    "state_flow_risk",
]

RepairHint = Literal[
    "reduce_nesting",
    "split_function",
    "remove_global_access",
    "pass_state_as_argument",
    "return_valid_python",
    "preserve_behavior",
    "use_standard_library",
    "use_registered_api",
    "remove_unsafe_call",
    "precompute_lookup",
    "fix_lint_error",
    "satisfy_contract",
    "guard_index_access",
    "return_updated_state",
]


@dataclass
class Violation:
    kind: ViolationKind
    engine: str
    severity: str
    summary: str
    rationale: str
    current_value: str
    allowed_value: str
    location: str = ""
    repair_hint: RepairHint | str = ""
    evidence: dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    is_compliant: bool
    violations: list[Violation] = field(default_factory=list)
