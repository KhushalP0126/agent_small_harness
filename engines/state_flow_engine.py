from __future__ import annotations

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding
from engines.decomposition_engine import DecompositionEngine, StructuralIR


class StateFlowEngine(BaseEngine):
    name = "engine-7-state-flow"

    def scan(self, source: str, ir: StructuralIR | None = None) -> list[EngineFinding]:
        ir = ir or DecompositionEngine().decompose(source)
        if not ir.state_flow_risks:
            return [
                EngineFinding(
                    engine=self.name,
                    severity="Low",
                    summary="No lost state-flow risk detected",
                    details="No helper was found assigning to a state-like parameter without returning the updated state.",
                    metrics={"risk_count": 0},
                )
            ]

        parameters = sorted({risk.parameter for risk in ir.state_flow_risks})
        functions = sorted({risk.function_name for risk in ir.state_flow_risks})
        lines = sorted({risk.line for risk in ir.state_flow_risks})
        return [
            EngineFinding(
                engine=self.name,
                severity="Medium",
                summary="Potential lost state update",
                details=(
                    "A helper assigns to a state-like parameter but does not return the updated state. "
                    "For parser or event-loop tasks, this often means the caller keeps using stale state."
                ),
                metrics={
                    "risk_count": len(ir.state_flow_risks),
                    "parameters": parameters,
                    "functions": functions,
                    "lines": lines,
                },
                diagnostic=EngineDiagnostic(
                    violation="STATE_FLOW_RISK",
                    threshold="state-changing helpers must return updated state",
                    actual=", ".join(f"{risk.function_name}({risk.parameter})" for risk in ir.state_flow_risks),
                    location=", ".join(f"line {line}" for line in lines),
                    recommended_refactor=(
                        "Return the updated state from the helper and assign it at the call site, or keep the "
                        "state transition in the caller so later operations see the new value."
                    ),
                ),
            )
        ]
