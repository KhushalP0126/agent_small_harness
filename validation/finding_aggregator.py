from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from validation.types import Violation


SEVERITY_RANK = {
    "info": 0,
    "low": 1,
    "warn": 2,
    "medium": 2,
    "block": 3,
    "high": 3,
}


@dataclass
class FunctionAnchor:
    name: str
    line_start: int
    line_end: int

    @property
    def ast_anchor(self) -> str:
        return f"FunctionDef:{self.name}:L{self.line_start}"


@dataclass
class RepairDirective:
    function_name: str
    ast_anchor: str
    location: str
    severity: str
    kinds: list[str]
    engines: list[str]
    summaries: list[str]
    repair_hints: list[str]
    instruction: str
    repeated: bool = False
    delta_summary: str = ""
    evidence: dict = field(default_factory=dict)


def _function_anchors(source: str) -> list[FunctionAnchor]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    anchors: list[FunctionAnchor] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            anchors.append(
                FunctionAnchor(
                    name=node.name,
                    line_start=getattr(node, "lineno", 0),
                    line_end=getattr(node, "end_lineno", getattr(node, "lineno", 0)),
                )
            )
    return sorted(anchors, key=lambda anchor: (anchor.line_start, anchor.name))


def _line_from_location(location: str) -> int | None:
    match = re.search(r"\bline\s+(\d+)\b", location or "")
    if not match:
        return None
    return int(match.group(1))


def _anchor_for_violation(source: str, violation: Violation, anchors: list[FunctionAnchor]) -> FunctionAnchor | None:
    line = _line_from_location(violation.location)
    if line is not None:
        for anchor in anchors:
            if anchor.line_start <= line <= anchor.line_end:
                return anchor
    if len(anchors) == 1:
        return anchors[0]
    return None


def _severity(values: Iterable[str]) -> str:
    severities = list(values)
    if not severities:
        return "Low"
    return max(severities, key=lambda value: SEVERITY_RANK.get(value.lower(), 0))


def _delta_lookup(diagnostic_deltas: list[dict] | None) -> dict[tuple[str, str], dict]:
    lookup: dict[tuple[str, str], dict] = {}
    for delta in diagnostic_deltas or []:
        lookup[(str(delta.get("engine", "")), str(delta.get("kind", "")))] = delta
    return lookup


def _join_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _directive_instruction(function_name: str, kinds: set[str], repair_hints: list[str], repeated: bool) -> str:
    target = f"`{function_name}`" if function_name != "<module>" else "the module-level code"
    prefix = "Repeated failure: " if repeated else ""

    if "parse_error" in kinds:
        return f"{prefix}Return valid Python source only before attempting deeper repair."
    if "behavior_mismatch" in kinds:
        return (
            f"{prefix}Repair {target} against the failing input/output cases first; preserve behavior and avoid "
            "constant or hardcoded shortcuts."
        )
    if "formal_counterexample" in kinds:
        return (
            f"{prefix}Repair {target} against the formal verifier output. Preserve behavior while satisfying "
            "all contracts, assertions, and symbolic counterexamples."
        )
    if "cyclomatic_complexity" in kinds and "algorithmic_cost" in kinds:
        return (
            f"{prefix}Refactor {target} structurally: remove repeated linear lookups with a precomputed set or "
            "dictionary, then split branch-heavy parsing or classification decisions into small helpers."
        )
    if "cyclomatic_complexity" in kinds and "loop_depth" in kinds:
        return (
            f"{prefix}Refactor {target} by extracting nested-loop and branch-heavy logic into helper functions; "
            "prefer guard clauses, lookup tables, and single-purpose helpers."
        )
    if "cyclomatic_complexity" in kinds:
        return (
            f"{prefix}Reduce decision paths in {target}. Extract helper functions and replace long if/elif chains "
            "with lookup tables, guard clauses, or arithmetic/table-driven logic."
        )
    if "algorithmic_cost" in kinds:
        return (
            f"{prefix}Improve algorithmic cost in {target}. Precompute constant-time lookups before loops and avoid "
            "membership checks against lists or tuples inside repeated iteration."
        )
    if "state_flow_risk" in kinds:
        return (
            f"{prefix}Repair state propagation in {target}. If a helper updates parser or event state, return the "
            "updated state and assign it at the call site so later lines observe the transition."
        )
    if "bounds_risk" in kinds:
        return (
            f"{prefix}Repair bounds safety in {target}. Do not index at len(seq) or iterate through "
            "range(len(seq) + 1); guard the index or iterate over valid positions only."
        )
    if "loop_depth" in kinds:
        return (
            f"{prefix}Reduce loop nesting in {target}. Move inner-loop work into helpers or flatten traversal where "
            "the behavior remains clear."
        )
    if "global_mutation" in kinds or "module_state_mutation" in kinds:
        return (
            f"{prefix}Remove shared-state mutation from {target}. Pass state explicitly and return new values instead "
            "of using globals or module-level containers."
        )
    if "lint_error" in kinds:
        return f"{prefix}Fix blocking lint issues in {target}, especially undefined names, invalid imports, and bad calls."
    if repair_hints:
        return f"{prefix}Apply repair hints for {target}: {', '.join(repair_hints)}."
    return f"{prefix}Repair {target} according to the grouped engine findings."


def aggregate_violations(
    source: str,
    violations: list[Violation],
    diagnostic_deltas: list[dict] | None = None,
) -> list[RepairDirective]:
    """Collapse raw policy violations into function-scoped repair directives.

    Engines intentionally stay narrow and independent. This layer is where their
    findings become a model-facing repair plan: nearby or same-function failures
    are grouped, repeated failures are marked, and one structural instruction is
    generated for each affected function.
    """

    anchors = _function_anchors(source)
    deltas = _delta_lookup(diagnostic_deltas)
    grouped: dict[str, list[Violation]] = {}
    group_anchors: dict[str, FunctionAnchor | None] = {}

    for violation in violations:
        anchor = _anchor_for_violation(source, violation, anchors)
        key = anchor.name if anchor is not None else violation.location or violation.kind
        if not key:
            key = "<module>"
        grouped.setdefault(key, []).append(violation)
        group_anchors.setdefault(key, anchor)

    directives: list[RepairDirective] = []
    for key, group in grouped.items():
        anchor = group_anchors.get(key)
        function_name = anchor.name if anchor is not None else ("<module>" if key == "<module>" else key)
        ast_anchor = anchor.ast_anchor if anchor is not None else ""
        kinds = set(violation.kind for violation in group)
        repair_hints = _join_unique(str(violation.repair_hint) for violation in group)
        delta_items = [
            deltas[(violation.engine, violation.kind)]
            for violation in group
            if (violation.engine, violation.kind) in deltas
        ]
        repeated = bool(delta_items)
        no_improvement = any(delta.get("delta") == 0 for delta in delta_items)
        delta_parts = []
        for delta in delta_items:
            prior = delta.get("prior_actual", "")
            current = delta.get("current_actual", "")
            change = "no improvement" if delta.get("delta") == 0 else (
                "improved" if delta.get("improved") else "changed"
            )
            delta_parts.append(f"{delta.get('kind')}: {prior} -> {current} ({change})")

        directives.append(
            RepairDirective(
                function_name=function_name,
                ast_anchor=ast_anchor,
                location=group[0].location,
                severity=_severity(violation.severity for violation in group),
                kinds=sorted(kinds),
                engines=_join_unique(violation.engine for violation in group),
                summaries=_join_unique(violation.summary for violation in group),
                repair_hints=repair_hints,
                instruction=_directive_instruction(
                    function_name=function_name,
                    kinds=kinds,
                    repair_hints=repair_hints,
                    repeated=repeated and no_improvement,
                ),
                repeated=repeated,
                delta_summary="; ".join(delta_parts),
                evidence={
                    "violations": [asdict(violation) for violation in group],
                },
            )
        )

    return sorted(
        directives,
        key=lambda directive: (
            -SEVERITY_RANK.get(directive.severity.lower(), 0),
            directive.function_name,
        ),
    )


def serialize_repair_directives(directives: list[RepairDirective]) -> list[dict]:
    return [asdict(directive) for directive in directives]
