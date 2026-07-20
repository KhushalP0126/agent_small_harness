from __future__ import annotations

import ast
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath


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
        unresolved = validate_imported_symbols(source, external_roots=external_roots)
        if unresolved:
            missing_symbols[path] = unresolved
    return FileImportGraph(
        files=sorted(files),
        imports_by_file=imports_by_file,
        missing_imports=missing_imports,
        missing_symbols=missing_symbols,
    )


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
