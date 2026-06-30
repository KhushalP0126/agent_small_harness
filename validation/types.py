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
    "unsafe_call",
]

RepairHint = Literal[
    "reduce_nesting",
    "split_function",
    "remove_global_access",
    "pass_state_as_argument",
    "return_valid_python",
    "preserve_behavior",
    "remove_unsafe_call",
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
