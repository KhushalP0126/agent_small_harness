from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from engines.base import BaseEngine, EngineFinding
from engines.treesitter_support import parse_tree


# Per-language node-type spec. The traversal logic is shared; only the node names differ.
LOOP_TYPES: dict[str, set[str]] = {
    "c": {"for_statement", "while_statement", "do_statement"},
    "cpp": {"for_statement", "while_statement", "do_statement", "for_range_loop"},
    "rust": {"for_expression", "while_expression", "loop_expression"},
    "javascript": {"for_statement", "while_statement", "do_statement", "for_in_statement"},
}
BRANCH_TYPES: dict[str, set[str]] = {
    "c": {"if_statement"},
    "cpp": {"if_statement"},
    "rust": {"if_expression", "match_expression"},
    "javascript": {"if_statement", "switch_statement"},
}
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


def _branch_types(language: str) -> set[str]:
    return BRANCH_TYPES.get(language, BRANCH_TYPES["c"])


def _callee_name(node: Any) -> str:
    if node is None:
        return ""
    if node.type == "identifier":
        return node.text.decode("utf-8", "replace")
    # Qualified / scoped / member calls: take the trailing identifier.
    if node.type in {
        "field_expression",
        "scoped_identifier",
        "qualified_identifier",
        "member_expression",
    }:
        text = node.text.decode("utf-8", "replace").strip()
        if text:
            return text
    return ""


def decompose(language: str, source: str) -> TreeSitterStructural:
    tree = parse_tree(language, source)
    loops = _loop_types(language)
    branches = _branch_types(language)
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
        elif node_type in branches:
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
        from engines.import_extractors import extract_imports
        from engines.import_risk import match_call_risks, match_import_risks
        from engines.base import EngineDiagnostic

        state = decompose(self.language, source)
        imports = extract_imports(self.language, source)
        hits = match_import_risks(self.language, imports)
        call_sites = [(name, 0) for name in state.unsafe_calls]
        # Also collect all call names from decompose path (unsafe_calls already filtered
        # by legacy set during traverse). Re-scan via risk table using those names and
        # a secondary full call walk for category coverage beyond UNSAFE_CALLS.
        hits.extend(match_call_risks(self.language, call_sites))
        # Full call scan for category table (process_exec, unsafe_memory, ...).
        from engines.treesitter_support import parse_tree

        try:
            tree = parse_tree(self.language, source)
        except Exception:
            tree = None
        if tree is not None:
            calls: list[tuple[str, int]] = []

            def visit(node):
                if node.type == "call_expression":
                    name = _callee_name(node.child_by_field_name("function"))
                    if name:
                        line = node.start_point[0] + 1 if hasattr(node, "start_point") else 0
                        calls.append((name, line))
                for child in node.children:
                    visit(child)

            visit(tree.root_node)
            hits.extend(match_call_risks(self.language, calls))

        seen: set[tuple[str, str, int, str]] = set()
        unique = []
        for hit in hits:
            key = (hit.category, hit.symbol, hit.line, hit.source)
            if key in seen:
                continue
            seen.add(key)
            unique.append(hit)

        if not unique:
            return [
                EngineFinding(
                    engine=self.name,
                    severity="Low",
                    summary="No unsafe API usage detected",
                    details="No denylisted unsafe APIs were found.",
                    metrics={"unsafe_calls": [], "risk_categories": []},
                )
            ]

        by_category: dict[str, list] = {}
        for hit in unique:
            by_category.setdefault(hit.category, []).append(hit)

        findings: list[EngineFinding] = []
        for category, category_hits in sorted(by_category.items()):
            enforcement = category_hits[0].enforcement
            symbols = sorted({hit.symbol for hit in category_hits})
            lines = sorted({hit.line for hit in category_hits if hit.line})
            severity = "High" if enforcement == "hard_block" else "Medium"
            # Keep legacy summary for unsafe_memory so older tests remain meaningful,
            # while also emitting stable risk_category metrics for policy.
            if category in {"unsafe_memory", "process_exec"} and enforcement == "hard_block":
                summary = "Unsafe API usage"
                details = "Unsafe C/C++ APIs detected: " + ", ".join(symbols) + "."
            elif enforcement == "hard_block":
                summary = f"Import risk ({category})"
                details = f"Hard-block category {category} matched: {', '.join(symbols)}."
            else:
                summary = f"Advisory import risk ({category})"
                details = f"Advisory category {category} matched: {', '.join(symbols)}."
            findings.append(
                EngineFinding(
                    engine=self.name,
                    severity=severity,
                    summary=summary,
                    details=details,
                    metrics={
                        "unsafe_calls": symbols,
                        "risk_category": category,
                        "enforcement": enforcement,
                        "symbols": symbols,
                        "language": self.language,
                        "lines": lines,
                    },
                    diagnostic=EngineDiagnostic(
                        violation="IMPORT_RISK_BLOCK"
                        if enforcement == "hard_block"
                        else "IMPORT_RISK_ADVISORY",
                        threshold=enforcement,
                        actual=", ".join(symbols),
                        location=", ".join(f"line {line}" for line in lines) or "call site",
                        recommended_refactor=f"Remove or replace {category} usage.",
                    ),
                )
            )
        return findings


def treesitter_engine_factories(language: str) -> list[Callable[[], BaseEngine]]:
    """Factories in Math/Hazards/Branching order, matching the Python engine set."""
    return [
        lambda: TreeSitterMathEngine(language),
        lambda: TreeSitterHazardsEngine(language),
        lambda: TreeSitterBranchingEngine(language),
    ]
