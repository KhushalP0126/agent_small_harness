from __future__ import annotations

import ast

from engines.base import BaseEngine, EngineFinding
from engines.decomposition_engine import DecompositionEngine


DECISION_NODES = (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.Assert, ast.IfExp)


class BranchingEngine(BaseEngine):
    name = "engine-3-branching"

    def scan(self, source: str) -> list[EngineFinding]:
        tree = ast.parse(source)
        ir = DecompositionEngine().decompose(source)
        decision_points = len(ir.loops) + len(ir.branches)
        for node in ast.walk(tree):
            if isinstance(node, (ast.ExceptHandler, ast.Assert, ast.IfExp)):
                decision_points += 1
            elif isinstance(node, ast.BoolOp):
                decision_points += max(0, len(node.values) - 1)
            elif isinstance(node, ast.comprehension):
                decision_points += len(node.ifs)

        branch_count = len(ir.branches)
        complexity = decision_points + 1
        if complexity >= 8:
            severity = "High"
            risk_level = "high"
        elif complexity >= 5:
            severity = "Medium"
            risk_level = "medium"
        else:
            severity = "Low"
            risk_level = "low"
        return [
            EngineFinding(
                engine=self.name,
                severity=severity,
                summary=f"Cyclomatic complexity {complexity} with {branch_count} conditional branches",
                details="Decision density estimates how many independent paths the code exposes.",
                metrics={
                    "cyclomatic_complexity": complexity,
                    "conditional_branch_count": branch_count,
                    "risk_level": risk_level,
                },
            )
        ]
