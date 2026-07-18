from __future__ import annotations

import ast
import sys

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding
from engines.decomposition_engine import DecompositionEngine, StructuralIR
from engines.library_registry import LibraryRegistry


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


def _external_imports(source: str, registered_libraries: set[str]) -> list[str]:
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _import_root(alias.name)
                if root not in ALLOWED_IMPORT_ROOTS and root not in registered_libraries:
                    imports.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            root = _import_root(node.module or "")
            if root and root not in ALLOWED_IMPORT_ROOTS and root not in registered_libraries:
                imports.add(root)
    return sorted(imports)


def _attribute_chain(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [*_attribute_chain(node.value), node.attr]
    return []


def _import_bindings(source: str, registry: LibraryRegistry) -> dict[str, tuple[str, str]]:
    tree = ast.parse(source)
    bindings: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _import_root(alias.name)
                if registry.is_registered(root):
                    local_name = alias.asname or root
                    bindings[local_name] = (root, "")
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            root = _import_root(node.module)
            if registry.is_registered(root):
                imported_path = node.module.split(".")[1:]
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    prefix = ".".join([*imported_path, alias.name])
                    bindings[local_name] = (root, prefix)
    return bindings


def _unknown_api_calls(source: str, registry: LibraryRegistry) -> list[dict]:
    tree = ast.parse(source)
    bindings = _import_bindings(source, registry)
    unknown: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _attribute_chain(node.func)
        if not chain:
            continue
        binding = bindings.get(chain[0])
        if binding is None:
            continue
        library, prefix = binding
        schema = registry.get(library)
        if schema is None:
            continue
        suffix = ".".join(chain[1:])
        call_path = ".".join(part for part in (prefix, suffix) if part)
        if call_path and call_path not in schema.allowed_calls:
            unknown.append(
                {
                    "library": library,
                    "call": call_path,
                    "line": getattr(node, "lineno", 0),
                    "allowed_calls": sorted(schema.allowed_calls),
                    "context": schema.context,
                    "repair": schema.unknown_api_repair,
                }
            )
    return unknown


class HazardsEngine(BaseEngine):
    name = "engine-2-hazards"

    def __init__(self, library_registry: LibraryRegistry | None = None) -> None:
        self.library_registry = library_registry or LibraryRegistry()

    def scan(self, source: str, ir: StructuralIR | None = None) -> list[EngineFinding]:
        ir = ir or DecompositionEngine().decompose(source)
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
                    diagnostic=EngineDiagnostic(
                        violation="GLOBAL_MUTATION",
                        actual=global_name,
                        location="global statement",
                        recommended_refactor=(
                            "Remove the global statement and pass state through explicit function arguments or return values."
                        ),
                    ),
                )
            )

        external_imports = _external_imports(source, self.library_registry.libraries())
        if external_imports:
            findings.append(
                EngineFinding(
                    engine=self.name,
                    severity="High",
                    summary="External dependency usage",
                    details="Generated Python source imports modules outside the standard library allowlist.",
                    metrics={"imports": external_imports},
                    diagnostic=EngineDiagnostic(
                        violation="EXTERNAL_DEPENDENCY",
                        threshold="standard library only",
                        actual=", ".join(external_imports),
                        location="import statement",
                        recommended_refactor=(
                            "Remove third-party imports and implement the required behavior with standard-library modules or local helpers."
                        ),
                    ),
                )
            )

        unknown_api_calls = _unknown_api_calls(source, self.library_registry)
        if unknown_api_calls:
            calls = [f"{item['library']}.{item['call']}" for item in unknown_api_calls]
            repairs = [item["repair"] for item in unknown_api_calls if item["repair"]]
            findings.append(
                EngineFinding(
                    engine=self.name,
                    severity="High",
                    summary="Unknown registered-library API usage",
                    details="Generated Python source calls APIs that are not present in the registered library schema.",
                    metrics={
                        "unknown_api_calls": calls,
                        "library_context": {
                            item["library"]: item["context"] for item in unknown_api_calls if item["context"]
                        },
                        "allowed_calls": {
                            item["library"]: item["allowed_calls"] for item in unknown_api_calls
                        },
                    },
                    diagnostic=EngineDiagnostic(
                        violation="UNKNOWN_API",
                        threshold="registered library schema",
                        actual=", ".join(calls),
                        location=", ".join(f"line {item['line']}" for item in unknown_api_calls),
                        recommended_refactor=repairs[0]
                        if repairs
                        else "Use only APIs listed in the registered library schema.",
                    ),
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
                    diagnostic=EngineDiagnostic(
                        violation="MODULE_STATE_MUTATION",
                        actual=", ".join(sorted(container_mutations)),
                        location="module-level container method call",
                        recommended_refactor=(
                            "Move mutable state into local scope, pass it as an explicit argument, or return a new value instead of mutating module state."
                        ),
                    ),
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
                    diagnostic=EngineDiagnostic(
                        violation="MODULE_STATE_MUTATION",
                        actual=", ".join(sorted(subscript_mutations)),
                        location="module-level subscript assignment",
                        recommended_refactor=(
                            "Replace indexed mutation of module state with local data construction and explicit return values."
                        ),
                    ),
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
