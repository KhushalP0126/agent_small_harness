from __future__ import annotations

import ast
from dataclasses import dataclass

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding


@dataclass(frozen=True)
class BoundsRisk:
    summary: str
    line: int
    expression: str
    severity: str = "Medium"


class _BoundsVisitor(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.risks: list[BoundsRisk] = []

    def _segment(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.source, node) or type(node).__name__

    def _name(self, node: ast.AST) -> str:
        return node.id if isinstance(node, ast.Name) else ""

    def _len_target(self, node: ast.AST) -> str:
        if not isinstance(node, ast.Call):
            return ""
        if not isinstance(node.func, ast.Name) or node.func.id != "len":
            return ""
        if len(node.args) != 1:
            return ""
        return self._name(node.args[0])

    def _is_len_of(self, node: ast.AST, container: str) -> bool:
        return bool(container and self._len_target(node) == container)

    def _index_is_len_or_beyond(self, index: ast.AST, container: str) -> bool:
        if self._is_len_of(index, container):
            return True
        if isinstance(index, ast.BinOp) and isinstance(index.op, ast.Add):
            if self._is_len_of(index.left, container):
                return True
        return False

    def visit_Subscript(self, node: ast.Subscript) -> None:
        container = self._name(node.value)
        if container and self._index_is_len_or_beyond(node.slice, container):
            mode = "write" if isinstance(node.ctx, ast.Store) else "read"
            self.risks.append(
                BoundsRisk(
                    summary=f"Potential out-of-bounds {mode}",
                    line=getattr(node, "lineno", 0),
                    expression=self._segment(node),
                )
            )
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._check_range_len_plus_one(node.iter)
        self.generic_visit(node)

    def _check_range_len_plus_one(self, node: ast.AST) -> None:
        if not isinstance(node, ast.Call):
            return
        if not isinstance(node.func, ast.Name) or node.func.id != "range":
            return
        if len(node.args) not in {1, 2}:
            return
        stop = node.args[0] if len(node.args) == 1 else node.args[1]
        if not isinstance(stop, ast.BinOp) or not isinstance(stop.op, ast.Add):
            return
        if not isinstance(stop.right, ast.Constant) or stop.right.value != 1:
            return
        container = self._len_target(stop.left)
        if container:
            self.risks.append(
                BoundsRisk(
                    summary="Potential range upper-bound overflow",
                    line=getattr(node, "lineno", 0),
                    expression=self._segment(node),
                )
            )


class BoundsEngine(BaseEngine):
    name = "engine-6-bounds"

    def scan(self, source: str) -> list[EngineFinding]:
        tree = ast.parse(source)
        visitor = _BoundsVisitor(source)
        visitor.visit(tree)
        if not visitor.risks:
            return [
                EngineFinding(
                    engine=self.name,
                    severity="Low",
                    summary="No high-confidence bounds risk detected",
                    details="No direct len(container) index or range(len(container) + 1) pattern was found.",
                    metrics={"risk_count": 0},
                )
            ]

        lines = sorted({risk.line for risk in visitor.risks})
        expressions = [risk.expression for risk in visitor.risks]
        summaries = sorted({risk.summary for risk in visitor.risks})
        return [
            EngineFinding(
                engine=self.name,
                severity="Medium",
                summary="Potential bounds risk",
                details=(
                    "The draft contains high-confidence index patterns that can read or write one past the end "
                    "of a sequence. This engine is warning-first because full bounds proof requires dataflow."
                ),
                metrics={
                    "risk_count": len(visitor.risks),
                    "lines": lines,
                    "expressions": expressions,
                    "risk_summaries": summaries,
                },
                diagnostic=EngineDiagnostic(
                    violation="BOUNDS_RISK",
                    threshold="index must stay within valid sequence bounds",
                    actual=", ".join(expressions),
                    location=", ".join(f"line {line}" for line in lines),
                    recommended_refactor=(
                        "Use guarded index checks, iterate over values directly, or use range(len(seq)) instead of "
                        "indexing at len(seq) or range(len(seq) + 1)."
                    ),
                ),
            )
        ]
