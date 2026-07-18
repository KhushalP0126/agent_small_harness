from __future__ import annotations

import ast

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding
from engines.decomposition_engine import DecompositionEngine, StructuralIR


class MathEngine(BaseEngine):
    name = "engine-1-math"

    def scan(self, source: str, ir: StructuralIR | None = None) -> list[EngineFinding]:
        ir = ir or DecompositionEngine().decompose(source)
        max_depth = max((loop.depth for loop in ir.loops), default=0)
        deepest_loop = next((loop for loop in ir.loops if loop.depth == max_depth), None)
        deepest_loop_types = [segment.split(":", 1)[0] for segment in (deepest_loop.path if deepest_loop else [])]
        metrics = {
            "max_loop_depth": max_depth,
            "loop_types": deepest_loop_types,
        }
        diagnostic = EngineDiagnostic(
            violation="LOOP_DEPTH_EXCEEDED" if max_depth > 2 else "",
            threshold="<= 2",
            actual=str(max_depth),
            location=f"line {deepest_loop.line}" if deepest_loop else "",
            recommended_refactor=(
                "Extract inner-loop work into helper functions or precompute lookup structures to keep nesting at depth 2 or less."
                if max_depth > 2
                else ""
            ),
        )
        if max_depth >= 3:
            severity = "High"
        elif max_depth == 2:
            severity = "Medium"
        else:
            severity = "Low"

        if max_depth > 1:
            return [
                EngineFinding(
                    engine=self.name,
                    severity=severity,
                    summary=f"Loop nesting depth {max_depth} detected",
                    details=(
                        "Nested iteration increases growth risk. Review whether the loop "
                        "structure can be flattened or precomputed."
                    ),
                    metrics=metrics,
                    diagnostic=diagnostic,
                )
            ]
        return [
            EngineFinding(
                engine=self.name,
                severity=severity,
                summary=f"Loop nesting depth {max_depth} detected",
                details="Control flow is compatible with linear benchmarking assumptions.",
                metrics=metrics,
                diagnostic=diagnostic,
            )
        ]
