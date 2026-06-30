from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from engines.base import BaseEngine, EngineFinding
from engines.treesitter_support import parse_tree


# Per-language node-type spec. The traversal logic is shared; only the node names differ.
LOOP_TYPES: dict[str, set[str]] = {
    "c": {"for_statement", "while_statement", "do_statement"},
    "cpp": {"for_statement", "while_statement", "do_statement", "for_range_loop"},
}
BRANCH_TYPES = {"if_statement"}
# Extra decision points beyond loops/ifs (switch cases, ternary, C++ exception handlers).
DECISION_EXTRA = {"case_statement", "conditional_expression", "catch_clause"}
LOGICAL_OPERATORS = {"&&", "||"}

# Security-focused denylist of unsafe C/C++ APIs (documented; extend as needed).
UNSAFE_CALLS = {
    "gets",
    "strcpy",
    "strcat",
    "sprintf",
    "vsprintf",
    "system",
    "popen",
}


@dataclass
class TreeSitterStructural:
    max_loop_depth: int = 0
    deepest_loop_types: list[str] = field(default_factory=list)
    decision_points: int = 0
    branch_count: int = 0
    unsafe_calls: list[str] = field(default_factory=list)


def _loop_types(language: str) -> set[str]:
    return LOOP_TYPES.get(language, LOOP_TYPES["c"])


def _callee_name(node: Any) -> str:
    if node is None:
        return ""
    if node.type == "identifier":
        return node.text.decode("utf-8", "replace")
    # Qualified / scoped / member calls: take the trailing identifier.
    if node.type in {"field_expression", "scoped_identifier", "qualified_identifier"}:
        identifiers = [child for child in node.children if child.type == "identifier"]
        if identifiers:
            return identifiers[-1].text.decode("utf-8", "replace")
    return ""


def decompose(language: str, source: str) -> TreeSitterStructural:
    tree = parse_tree(language, source)
    loops = _loop_types(language)
    state = TreeSitterStructural()

    def visit(node: Any, loop_path: list[str]) -> None:
        node_type = node.type
        current_path = loop_path
        if node_type in loops:
            current_path = loop_path + [node_type]
            if len(current_path) > state.max_loop_depth:
                state.max_loop_depth = len(current_path)
                state.deepest_loop_types = list(current_path)
            state.decision_points += 1
        elif node_type in BRANCH_TYPES:
            state.decision_points += 1
            state.branch_count += 1
        elif node_type in DECISION_EXTRA:
            state.decision_points += 1
        elif node_type == "binary_expression":
            for child in node.children:
                if child.type in LOGICAL_OPERATORS:
                    state.decision_points += 1
        elif node_type == "call_expression":
            name = _callee_name(node.child_by_field_name("function"))
            if name in UNSAFE_CALLS:
                state.unsafe_calls.append(name)
        for child in node.children:
            visit(child, current_path)

    visit(tree.root_node, [])
    return state


class _TreeSitterEngine(BaseEngine):
    def __init__(self, language: str) -> None:
        self.language = language.strip().lower()


class TreeSitterMathEngine(_TreeSitterEngine):
    name = "engine-1-math"

    def scan(self, source: str) -> list[EngineFinding]:
        state = decompose(self.language, source)
        depth = state.max_loop_depth
        severity = "High" if depth >= 3 else "Medium" if depth == 2 else "Low"
        details = (
            "Nested iteration increases growth risk. Review whether the loop "
            "structure can be flattened or precomputed."
            if depth > 1
            else "Control flow is compatible with linear benchmarking assumptions."
        )
        return [
            EngineFinding(
                engine=self.name,
                severity=severity,
                summary=f"Loop nesting depth {depth} detected",
                details=details,
                metrics={"max_loop_depth": depth, "loop_types": state.deepest_loop_types},
            )
        ]


class TreeSitterBranchingEngine(_TreeSitterEngine):
    name = "engine-3-branching"

    def scan(self, source: str) -> list[EngineFinding]:
        state = decompose(self.language, source)
        complexity = state.decision_points + 1
        if complexity >= 8:
            severity, risk_level = "High", "high"
        elif complexity >= 5:
            severity, risk_level = "Medium", "medium"
        else:
            severity, risk_level = "Low", "low"
        return [
            EngineFinding(
                engine=self.name,
                severity=severity,
                summary=f"Cyclomatic complexity {complexity} with {state.branch_count} conditional branches",
                details="Decision density estimates how many independent paths the code exposes.",
                metrics={
                    "cyclomatic_complexity": complexity,
                    "conditional_branch_count": state.branch_count,
                    "risk_level": risk_level,
                },
            )
        ]


class TreeSitterHazardsEngine(_TreeSitterEngine):
    name = "engine-2-hazards"

    def scan(self, source: str) -> list[EngineFinding]:
        state = decompose(self.language, source)
        if state.unsafe_calls:
            names = sorted(set(state.unsafe_calls))
            return [
                EngineFinding(
                    engine=self.name,
                    severity="High",
                    summary="Unsafe API usage",
                    details="Unsafe C/C++ APIs detected: " + ", ".join(names) + ".",
                    metrics={"unsafe_calls": names},
                )
            ]
        return [
            EngineFinding(
                engine=self.name,
                severity="Low",
                summary="No unsafe API usage detected",
                details="No denylisted unsafe APIs were found.",
                metrics={"unsafe_calls": []},
            )
        ]


def treesitter_engine_factories(language: str) -> list[Callable[[], BaseEngine]]:
    """Factories in Math/Hazards/Branching order, matching the Python engine set."""
    return [
        lambda: TreeSitterMathEngine(language),
        lambda: TreeSitterHazardsEngine(language),
        lambda: TreeSitterBranchingEngine(language),
    ]
