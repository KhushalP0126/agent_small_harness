from __future__ import annotations

from dataclasses import dataclass

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding
from engines.decomposition_engine import DecompositionEngine, StructuralIR


@dataclass
class MembershipHotspot:
    container: str
    line: int


class CostEngine(BaseEngine):
    name = "engine-4-cost"

    def scan(self, source: str, ir: StructuralIR | None = None) -> list[EngineFinding]:
        ir = ir or DecompositionEngine().decompose(source)
        hotspots = [
            MembershipHotspot(container=check.container, line=check.line)
            for check in ir.membership_checks
            if ir.kind_of(check.container, check.scope_path, check.line) in {"list", "tuple"}
        ]
        if hotspots:
            containers = sorted({hotspot.container for hotspot in hotspots})
            lines = sorted({hotspot.line for hotspot in hotspots})
            return [
                EngineFinding(
                    engine=self.name,
                    severity="High",
                    summary="Linear membership test inside loop",
                    details=(
                        "A loop performs membership checks against a non-set container. "
                        "This often creates O(N*M) behavior and should usually be converted to set lookup."
                    ),
                    metrics={
                        "containers": containers,
                        "lines": lines,
                        "hotspot_count": len(hotspots),
                    },
                    diagnostic=EngineDiagnostic(
                        violation="ALGORITHMIC_COST",
                        threshold="avoid repeated linear membership inside loops",
                        actual=", ".join(containers),
                        location=", ".join(f"line {line}" for line in lines),
                        recommended_refactor=(
                            "Precompute a set for repeated membership checks before the loop, then test against that set."
                        ),
                    ),
                )
            ]
        return [
            EngineFinding(
                engine=self.name,
                severity="Low",
                summary="No repeated linear membership hotspot detected",
                details="No loop membership checks against non-set containers were found.",
                metrics={"hotspot_count": 0},
            )
        ]
