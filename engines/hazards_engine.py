from __future__ import annotations

import ast

from engines.base import BaseEngine, EngineFinding
from engines.decomposition_engine import DecompositionEngine


MUTATING_METHODS = {
    "append",
    "extend",
    "insert",
    "pop",
    "remove",
    "clear",
    "sort",
    "reverse",
    "add",
    "discard",
    "update",
    "setdefault",
}

class HazardsEngine(BaseEngine):
    name = "engine-2-hazards"

    def scan(self, source: str) -> list[EngineFinding]:
        ir = DecompositionEngine().decompose(source)
        findings: list[EngineFinding] = []
        container_mutations = {
            mutation.target
            for mutation in ir.mutations
            if mutation.target in ir.module_state_names and mutation.mutation_type.startswith("call.")
        }
        subscript_mutations = {
            mutation.target
            for mutation in ir.mutations
            if mutation.target in ir.module_state_names and "subscript" in mutation.mutation_type
        }

        for global_name in ir.explicit_globals:
                findings.append(
                    EngineFinding(
                        engine=self.name,
                        severity="High",
                        summary="Global mutation hazard",
                        details="Global statements increase coupling across generated paths.",
                        metrics={"global_names": [global_name]},
                    )
                )

        if container_mutations:
            findings.append(
                EngineFinding(
                    engine=self.name,
                    severity="High",
                    summary="Module-level container mutation hazard",
                    details="Module-scope containers are being mutated through method calls.",
                    metrics={"container_names": sorted(container_mutations)},
                )
            )
        if subscript_mutations:
            findings.append(
                EngineFinding(
                    engine=self.name,
                    severity="High",
                    summary="Module-level subscript mutation hazard",
                    details="Module-scope containers are being mutated through indexed assignment.",
                    metrics={"container_names": sorted(subscript_mutations)},
                )
            )
        if findings:
            return findings
        return [
            EngineFinding(
                engine=self.name,
                severity="Low",
                summary="No global mutation hazard detected",
                details="Generated source does not declare global variables.",
                metrics={"module_state_names": ir.module_state_names},
            )
        ]
