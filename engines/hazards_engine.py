from __future__ import annotations

import ast
import sys

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

ALLOWED_IMPORT_ROOTS = set(getattr(sys, "stdlib_module_names", set())) | {"__future__"}


def _import_root(name: str) -> str:
    return name.split(".", 1)[0]


def _external_imports(source: str) -> list[str]:
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _import_root(alias.name)
                if root not in ALLOWED_IMPORT_ROOTS:
                    imports.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            root = _import_root(node.module or "")
            if root and root not in ALLOWED_IMPORT_ROOTS:
                imports.add(root)
    return sorted(imports)


class HazardsEngine(BaseEngine):
    name = "engine-2-hazards"

    def scan(self, source: str) -> list[EngineFinding]:
        ir = DecompositionEngine().decompose(source)
        findings: list[EngineFinding] = []
        container_mutations = {
            mutation.target
            for mutation in ir.mutations
            if mutation.target in ir.module_state_names
            and mutation.mutation_type.startswith("call.")
            and mutation.mutation_type.split(".", 1)[1] in MUTATING_METHODS
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

        external_imports = _external_imports(source)
        if external_imports:
            findings.append(
                EngineFinding(
                    engine=self.name,
                    severity="High",
                    summary="External dependency usage",
                    details="Generated Python source imports modules outside the standard library allowlist.",
                    metrics={"imports": external_imports},
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
