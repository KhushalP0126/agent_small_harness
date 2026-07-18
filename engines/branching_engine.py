from __future__ import annotations

import ast

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding
from engines.decomposition_engine import DecompositionEngine, StructuralIR


DECISION_NODES = (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.Assert, ast.IfExp)


class _FunctionComplexityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.function_complexities: dict[str, int] = {}
        self.function_branch_counts: dict[str, int] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node)

    def _record_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        decision_points = 0
        branch_count = 0
        for child in ast.walk(node):
            if child is node:
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.If):
                decision_points += 1
                branch_count += 1
            elif isinstance(child, (ast.For, ast.While, ast.ExceptHandler, ast.Assert, ast.IfExp)):
                decision_points += 1
            elif isinstance(child, ast.BoolOp):
                decision_points += max(0, len(child.values) - 1)
            elif isinstance(child, ast.comprehension):
                decision_points += len(child.ifs)
        self.function_complexities[node.name] = decision_points + 1
        self.function_branch_counts[node.name] = branch_count


class BranchingEngine(BaseEngine):
    name = "engine-3-branching"

    def scan(self, source: str, ir: StructuralIR | None = None) -> list[EngineFinding]:
        tree = ast.parse(source)
        ir = ir or DecompositionEngine().decompose(source)
        decision_points = len(ir.loops) + len(ir.branches)
        for node in ast.walk(tree):
            if isinstance(node, (ast.ExceptHandler, ast.Assert, ast.IfExp)):
                decision_points += 1
            elif isinstance(node, ast.BoolOp):
                decision_points += max(0, len(node.values) - 1)
            elif isinstance(node, ast.comprehension):
                decision_points += len(node.ifs)

        branch_count = len(ir.branches)
        module_complexity = decision_points + 1
        function_visitor = _FunctionComplexityVisitor()
        function_visitor.visit(tree)
        function_complexities = function_visitor.function_complexities
        max_function_complexity = max(function_complexities.values(), default=module_complexity)
        blocking_complexity = max_function_complexity
        if blocking_complexity >= 8:
            severity = "High"
            risk_level = "high"
        elif blocking_complexity >= 5:
            severity = "Medium"
            risk_level = "medium"
        else:
            severity = "Low"
            risk_level = "low"
        worst_function = ""
        if function_complexities:
            worst_function = max(function_complexities, key=lambda name: function_complexities[name])
        diagnostic = EngineDiagnostic(
            violation="CYCLOMATIC_COMPLEXITY_EXCEEDED" if blocking_complexity > 7 else "",
            threshold="<= 7",
            actual=str(blocking_complexity),
            location=(
                f"function {worst_function}"
                if worst_function
                else f"{branch_count} conditional branches"
            ),
            recommended_refactor=(
                "Extract branch-heavy decisions into small helper functions and replace repeated if/elif chains with lookup tables or guard clauses."
                if blocking_complexity > 7
                else ""
            ),
        )
        return [
            EngineFinding(
                engine=self.name,
                severity=severity,
                summary=(
                    f"Max function cyclomatic complexity {max_function_complexity}; "
                    f"module complexity {module_complexity} with {branch_count} conditional branches"
                ),
                details="Decision density estimates how many independent paths the code exposes.",
                metrics={
                    "cyclomatic_complexity": blocking_complexity,
                    "module_cyclomatic_complexity": module_complexity,
                    "max_function_cyclomatic_complexity": max_function_complexity,
                    "function_complexities": function_complexities,
                    "function_branch_counts": function_visitor.function_branch_counts,
                    "conditional_branch_count": branch_count,
                    "risk_level": risk_level,
                },
                diagnostic=diagnostic,
            )
        ]
