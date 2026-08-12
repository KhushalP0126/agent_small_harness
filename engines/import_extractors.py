"""Language-agnostic import extraction into StructuralIR ImportRecord values."""

from __future__ import annotations

import ast
import re
from typing import Any

from engines.decomposition_engine import ImportRecord


def extract_imports(language: str, source: str) -> list[ImportRecord]:
    language = language.strip().lower()
    if language == "python":
        return _extract_python(source)
    if language in {"c", "cpp"}:
        return _extract_c_family(language, source)
    if language == "rust":
        return _extract_rust(source)
    if language in {"javascript", "js", "node", "nodejs"}:
        return _extract_javascript(source)
    return []


def _extract_python(source: str) -> list[ImportRecord]:
    tree = ast.parse(source)
    records: list[ImportRecord] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                records.append(
                    ImportRecord(
                        name=alias.name,
                        language="python",
                        kind="module",
                        line=getattr(node, "lineno", 0),
                        bound_symbols=[local],
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative imports stay module-kind but keep dotted path empty-safe.
                module_name = "." * node.level + (node.module or "")
            else:
                module_name = node.module or ""
            bound = [alias.asname or alias.name for alias in node.names]
            records.append(
                ImportRecord(
                    name=module_name or ".",
                    language="python",
                    kind="module",
                    line=getattr(node, "lineno", 0),
                    bound_symbols=bound,
                )
            )
    return records


def _node_text(node: Any) -> str:
    raw = getattr(node, "text", None)
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw)


def _extract_c_family(language: str, source: str) -> list[ImportRecord]:
    try:
        from engines.treesitter_support import is_available, parse_tree
    except Exception:
        return _extract_c_family_fallback(language, source)
    if not is_available() or language not in {"c", "cpp"}:
        return _extract_c_family_fallback(language, source)
    try:
        tree = parse_tree(language, source)
    except Exception:
        return _extract_c_family_fallback(language, source)

    records: list[ImportRecord] = []

    def visit(node: Any) -> None:
        if node.type == "preproc_include":
            path = ""
            for child in node.children:
                if child.type in {"string_literal", "system_lib_string", "\"", "<"}:
                    text = _node_text(child).strip()
                    if text and text not in {"#include", "include"}:
                        path = text.strip("\"<> ")
                        if path:
                            break
                elif child.type == "path":
                    path = _node_text(child).strip("\"<> ")
            if not path:
                text = _node_text(node)
                match = re.search(r"#\s*include\s*[<\"]([^>\"]+)[>\"]", text)
                if match:
                    path = match.group(1)
            if path:
                records.append(
                    ImportRecord(
                        name=path,
                        language=language,
                        kind="header",
                        line=(node.start_point[0] + 1) if hasattr(node, "start_point") else 0,
                        bound_symbols=[],
                    )
                )
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return records


def _extract_c_family_fallback(language: str, source: str) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for index, line in enumerate(source.splitlines(), start=1):
        match = re.match(r"\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", line)
        if match:
            records.append(
                ImportRecord(
                    name=match.group(1),
                    language=language,
                    kind="header",
                    line=index,
                    bound_symbols=[],
                )
            )
    return records


def _extract_rust(source: str) -> list[ImportRecord]:
    try:
        from engines.treesitter_support import is_language_available, parse_tree

        if is_language_available("rust"):
            return _extract_rust_treesitter(source)
    except Exception:
        pass
    return _extract_rust_fallback(source)


def _extract_rust_treesitter(source: str) -> list[ImportRecord]:
    from engines.treesitter_support import parse_tree

    tree = parse_tree("rust", source)
    records: list[ImportRecord] = []

    def path_from_use(node: Any) -> str:
        return " ".join(_node_text(node).split()).removeprefix("use ").rstrip(";").strip()

    def visit(node: Any) -> None:
        if node.type == "use_declaration":
            name = path_from_use(node)
            if name:
                records.append(
                    ImportRecord(
                        name=name,
                        language="rust",
                        kind="crate",
                        line=node.start_point[0] + 1,
                        bound_symbols=[],
                    )
                )
        elif node.type == "extern_crate_declaration":
            text = " ".join(_node_text(node).split())
            name = text.removeprefix("extern crate ").rstrip(";").strip()
            if name:
                records.append(
                    ImportRecord(
                        name=name,
                        language="rust",
                        kind="crate",
                        line=node.start_point[0] + 1,
                        bound_symbols=[],
                    )
                )
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return records


def _extract_rust_fallback(source: str) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for index, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("use "):
            name = stripped.removeprefix("use ").rstrip(";").strip()
            records.append(ImportRecord(name=name, language="rust", kind="crate", line=index))
        elif stripped.startswith("extern crate "):
            name = stripped.removeprefix("extern crate ").rstrip(";").strip()
            records.append(ImportRecord(name=name, language="rust", kind="crate", line=index))
    return records


def _extract_javascript(source: str) -> list[ImportRecord]:
    try:
        from engines.treesitter_support import is_language_available, parse_tree

        if is_language_available("javascript"):
            return _extract_javascript_treesitter(source)
    except Exception:
        pass
    return _extract_javascript_fallback(source)


def _extract_javascript_treesitter(source: str) -> list[ImportRecord]:
    from engines.treesitter_support import parse_tree

    tree = parse_tree("javascript", source)
    records: list[ImportRecord] = []

    def string_value(node: Any) -> str:
        text = _node_text(node).strip()
        if len(text) >= 2 and text[0] in {"'", '"', "`"} and text[-1] == text[0]:
            return text[1:-1]
        return text.strip("'\"`")

    def visit(node: Any) -> None:
        if node.type == "import_statement":
            source_node = node.child_by_field_name("source")
            name = string_value(source_node) if source_node is not None else ""
            if not name:
                text = _node_text(node)
                match = re.search(r"""from\s+['"]([^'"]+)['"]""", text)
                if match:
                    name = match.group(1)
            if name:
                records.append(
                    ImportRecord(
                        name=name,
                        language="javascript",
                        kind="module",
                        line=node.start_point[0] + 1,
                        bound_symbols=[],
                    )
                )
        elif node.type == "call_expression":
            func = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if func is not None and _node_text(func) == "require" and args is not None:
                for child in args.children:
                    if child.type in {"string", "template_string"}:
                        records.append(
                            ImportRecord(
                                name=string_value(child),
                                language="javascript",
                                kind="require",
                                line=node.start_point[0] + 1,
                                bound_symbols=[],
                            )
                        )
                        break
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return records


def _extract_javascript_fallback(source: str) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for index, line in enumerate(source.splitlines(), start=1):
        for match in re.finditer(r"""import\s+(?:[^'"]+\s+from\s+)?['"]([^'"]+)['"]""", line):
            records.append(
                ImportRecord(name=match.group(1), language="javascript", kind="module", line=index)
            )
        for match in re.finditer(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""", line):
            records.append(
                ImportRecord(name=match.group(1), language="javascript", kind="require", line=index)
            )
    return records
