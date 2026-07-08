from __future__ import annotations

import ast
from dataclasses import dataclass

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding


STATE_NAME_HINTS = (
    "state",
    "section",
    "current",
    "context",
    "ctx",
    "total",
    "balance",
)


@dataclass(frozen=True)
class StateFlowRisk:
    function_name: str
    line: int
    parameter: str


class _StateFlowVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.risks: list[StateFlowRisk] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        parameter_names = {arg.arg for arg in node.args.args}
        state_parameters = {
            name
            for name in parameter_names
            if any(hint in name.lower() for hint in STATE_NAME_HINTS)
        }
        if not state_parameters:
            self.generic_visit(node)
            return

        assigned_state_parameters: set[str] = set()
        returns_value = False
        returned_names: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store) and child.id in state_parameters:
                assigned_state_parameters.add(child.id)
            elif isinstance(child, ast.Return):
                if child.value is not None:
                    returns_value = True
                if isinstance(child.value, ast.Name):
                    returned_names.add(child.value.id)
                elif isinstance(child.value, (ast.Tuple, ast.List)):
                    returned_names.update(
                        item.id for item in child.value.elts if isinstance(item, ast.Name)
                    )

        if not assigned_state_parameters:
            self.generic_visit(node)
            return

        lost_parameters = (
            assigned_state_parameters
            if not returns_value
            else assigned_state_parameters - returned_names
        )
        for parameter in sorted(lost_parameters):
            self.risks.append(
                StateFlowRisk(
                    function_name=node.name,
                    line=getattr(node, "lineno", 0),
                    parameter=parameter,
                )
            )
        self.generic_visit(node)


class StateFlowEngine(BaseEngine):
    name = "engine-7-state-flow"

    def scan(self, source: str) -> list[EngineFinding]:
        tree = ast.parse(source)
        visitor = _StateFlowVisitor()
        visitor.visit(tree)
        if not visitor.risks:
            return [
                EngineFinding(
                    engine=self.name,
                    severity="Low",
                    summary="No lost state-flow risk detected",
                    details="No helper was found assigning to a state-like parameter without returning the updated state.",
                    metrics={"risk_count": 0},
                )
            ]

        parameters = sorted({risk.parameter for risk in visitor.risks})
        functions = sorted({risk.function_name for risk in visitor.risks})
        lines = sorted({risk.line for risk in visitor.risks})
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
                    "risk_count": len(visitor.risks),
                    "parameters": parameters,
                    "functions": functions,
                    "lines": lines,
                },
                diagnostic=EngineDiagnostic(
                    violation="STATE_FLOW_RISK",
                    threshold="state-changing helpers must return updated state",
                    actual=", ".join(f"{risk.function_name}({risk.parameter})" for risk in visitor.risks),
                    location=", ".join(f"line {line}" for line in lines),
                    recommended_refactor=(
                        "Return the updated state from the helper and assign it at the call site, or keep the "
                        "state transition in the caller so later operations see the new value."
                    ),
                ),
            )
        ]
