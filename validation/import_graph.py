from __future__ import annotations

import ast
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable


@dataclass(frozen=True)
class FileImportGraph:
    files: list[str]
    imports_by_file: dict[str, list[str]] = field(default_factory=dict)
    missing_imports: dict[str, list[str]] = field(default_factory=dict)
    missing_symbols: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_compliant(self) -> bool:
        return not any(self.missing_imports.values()) and not any(self.missing_symbols.values())


STDLIB_ROOTS = set(getattr(sys, "stdlib_module_names", set())) | {"__future__"}


def analyze_import_graph(files: dict[str, str], external_roots: set[str] | None = None) -> FileImportGraph:
    external_roots = external_roots or set()
    module_to_file = {_module_name(path): path for path in files}
    imports_by_file: dict[str, list[str]] = {}
    missing_imports: dict[str, list[str]] = {}
    missing_symbols: dict[str, list[str]] = {}
    for path, source in files.items():
        imports = _local_imports(source, module_to_file, external_roots)
        imports_by_file[path] = sorted(imports)
        missing = sorted(module for module in imports if module not in module_to_file)
        if missing:
            missing_imports[path] = missing
        unresolved = [
            *validate_local_imported_symbols(source, module_to_file, files),
            *validate_imported_symbols(source, external_roots=external_roots),
        ]
        if unresolved:
            missing_symbols[path] = unresolved
    return FileImportGraph(
        files=sorted(files),
        imports_by_file=imports_by_file,
        missing_imports=missing_imports,
        missing_symbols=missing_symbols,
    )


def validate_local_imported_symbols(
    source: str,
    module_to_file: dict[str, str],
    files: dict[str, str],
) -> list[str]:
    """Validate names imported from generated sibling modules."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    exports_by_module = {
        module: _defined_symbols(files[path])
        for module, path in module_to_file.items()
    }
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level:
            continue
        module = node.module or ""
        if module not in module_to_file:
            continue
        exports = exports_by_module[module]
        for alias in node.names:
            if alias.name != "*" and alias.name not in exports:
                missing.append(f"{module}.{alias.name}")
    return sorted(set(missing))


def _defined_symbols(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            symbols.update(target.id for target in targets if isinstance(target, ast.Name))
    return symbols


def validate_cross_file_contracts(
    files: dict[str, str],
    contracts: Iterable[Any],
) -> list[dict[str, str]]:
    """Check that owned contract exports exist with compatible call shapes."""

    parsed: dict[str, ast.Module] = {}
    for path, source in files.items():
        try:
            parsed[path] = ast.parse(source)
        except SyntaxError:
            continue
    issues: list[dict[str, str]] = []
    for contract in contracts:
        target_file = str(getattr(contract, "target_file", ""))
        name = str(getattr(contract, "name", ""))
        if not target_file or not name or target_file not in parsed:
            continue
        nodes = {
            node.name: node
            for node in parsed[target_file].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        node = nodes.get(name)
        if node is None:
            issues.append({"kind": "missing_contract_export", "file": target_file, "symbol": name})
            continue
        if getattr(contract, "kind", "function") == "class" and not isinstance(node, ast.ClassDef):
            issues.append({"kind": "contract_kind_mismatch", "file": target_file, "symbol": name})
            continue
        if getattr(contract, "kind", "function") != "class" and isinstance(node, ast.ClassDef):
            issues.append({"kind": "contract_kind_mismatch", "file": target_file, "symbol": name})
            continue
        expected = _signature_shape(str(getattr(contract, "signature", "")))
        actual = _node_signature_shape(node)
        if expected and actual and expected != actual:
            issues.append(
                {
                    "kind": "contract_signature_mismatch",
                    "file": target_file,
                    "symbol": name,
                    "expected": repr(expected),
                    "actual": repr(actual),
                }
            )
    return issues


def _signature_shape(signature: str) -> tuple[str, ...]:
    text = signature.strip()
    if not text.startswith("def "):
        return ()
    try:
        node = ast.parse(text + ":\n    pass\n").body[0]
    except SyntaxError:
        return ()
    return _node_signature_shape(node)


def _node_signature_shape(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ()
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    return tuple(arg.arg for arg in args) + (("*" if node.args.vararg else ""), ("**" if node.args.kwarg else ""))


def validate_imported_symbols(source: str, external_roots: set[str] | None = None) -> list[str]:
    """Return absolute imported symbols that do not exist in real modules.

    Local modules remain the import graph's responsibility. This check is for
    stdlib and explicitly allowed installed packages, where importing the real
    module lets the harness reject hallucinated ``from module import name``
    statements before executing generated code.
    """

    external_roots = external_roots or set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
            continue
        root = node.module.split(".", 1)[0]
        if root not in STDLIB_ROOTS and root not in external_roots:
            continue
        try:
            module = importlib.import_module(node.module)
        except Exception:
            missing.append(node.module)
            continue
        for alias in node.names:
            if alias.name == "*" or hasattr(module, alias.name):
                continue
            try:
                importlib.import_module(f"{node.module}.{alias.name}")
            except Exception:
                missing.append(f"{node.module}.{alias.name}")
    return sorted(set(missing))


def _module_name(path: str) -> str:
    pure = PurePosixPath(path)
    if pure.suffix == ".py":
        pure = pure.with_suffix("")
    return ".".join(part for part in pure.parts if part and part != "__init__")


def _local_imports(source: str, module_to_file: dict[str, str], external_roots: set[str]) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    known_roots = {module.split(".", 1)[0] for module in module_to_file}
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in known_roots or _looks_local_root(root, external_roots):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root in known_roots or _looks_local_root(root, external_roots):
                imports.add(module)
    return imports


def _looks_local_root(root: str, external_roots: set[str]) -> bool:
    return bool(root) and root not in STDLIB_ROOTS and root not in external_roots
