from __future__ import annotations

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding
from engines.decomposition_engine import DecompositionEngine, StructuralIR


class BoundsEngine(BaseEngine):
    name = "engine-6-bounds"

    def scan(self, source: str, ir: StructuralIR | None = None) -> list[EngineFinding]:
        ir = ir or DecompositionEngine().decompose(source)
        if not ir.bounds_risks:
            return [
                EngineFinding(
                    engine=self.name,
                    severity="Low",
                    summary="No high-confidence bounds risk detected",
                    details="No direct len(container) index or range(len(container) + 1) pattern was found.",
                    metrics={"risk_count": 0},
                )
            ]

        lines = sorted({risk.line for risk in ir.bounds_risks})
        expressions = [risk.expression for risk in ir.bounds_risks]
        summaries = sorted({risk.summary for risk in ir.bounds_risks})
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
                    "risk_count": len(ir.bounds_risks),
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
