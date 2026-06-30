from __future__ import annotations

import ast

from engines.base import BaseEngine, EngineFinding
from engines.decomposition_engine import DecompositionEngine


class MathEngine(BaseEngine):
    name = "engine-1-math"

    def scan(self, source: str) -> list[EngineFinding]:
        ir = DecompositionEngine().decompose(source)
        max_depth = max((loop.depth for loop in ir.loops), default=0)
        deepest_loop = next((loop for loop in ir.loops if loop.depth == max_depth), None)
        deepest_loop_types = [segment.split(":", 1)[0] for segment in (deepest_loop.path if deepest_loop else [])]
        metrics = {
            "max_loop_depth": max_depth,
            "loop_types": deepest_loop_types,
        }
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
                )
            ]
        return [
            EngineFinding(
                engine=self.name,
                severity=severity,
                summary=f"Loop nesting depth {max_depth} detected",
                details="Control flow is compatible with linear benchmarking assumptions.",
                metrics=metrics,
            )
        ]
