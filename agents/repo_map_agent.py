from __future__ import annotations

import ast
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agents.base import AgentResult, BaseAgent
from engines.decomposition_engine import DecompositionEngine


DEFAULT_SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        "artifacts",
        "data",
        "build",
        "dist",
    }
)

_STDLIB_NAMES = frozenset(getattr(sys, "stdlib_module_names", frozenset()))

# Compact-context caps keep the Plan Mode injection small; the full JSON graph is
# always available through the CLI for on-demand review.
_MAX_CONTEXT_FILES = 40
_MAX_CONTEXT_FUNCTIONS = 12
_MAX_CONTEXT_IMPORTS = 10
_MAX_CONTEXT_EDGES = 40


@dataclass
class FunctionInfo:
    name: str
    qualified_name: str = ""
    args: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    returns: str = "none"
    line: int = 0


@dataclass
class ImportInfo:
    module: str
    kind: str = "third_party"
    names: list[str] = field(default_factory=list)
    level: int = 0
    line: int = 0


@dataclass
class LoopSite:
    loop_type: str
    depth: int
    line: int


@dataclass
class VariableInfo:
    name: str
    scope: str
    kind: str
    line: int


@dataclass
class MutationInfo:
    target: str
    scope: str
    mutation_type: str
    line: int


@dataclass
class GraphNode:
    id: str
    kind: str
    label: str
    module: str = ""
    line: int = 0


@dataclass
class GraphEdge:
    source: str
    target: str
    kind: str
    label: str = ""
    line: int = 0


@dataclass
class FileRecord:
    path: str
    module: str
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    module_vars: list[str] = field(default_factory=list)
    class_vars: list[str] = field(default_factory=list)
    instance_vars: list[str] = field(default_factory=list)
    variables: list[VariableInfo] = field(default_factory=list)
    loops: list[LoopSite] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    mutations: list[MutationInfo] = field(default_factory=list)
    parse_error: str = ""

    @property
    def max_loop_depth(self) -> int:
        return max((loop.depth for loop in self.loops), default=0)


@dataclass
class RepoGraph:
    root: str
    files: list[FileRecord] = field(default_factory=list)
    local_modules: list[str] = field(default_factory=list)
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


class RepoMapAgent(BaseAgent):
    """Walk a Python repository and map its structure with the standard-library ``ast``.

    The map is deliberately cheap and stateless: it re-runs per task instead of
    caching so it never goes stale as the worker edits files. It reuses the same
    AST decomposition the static engines use for loop sites/depth, then adds the
    per-function call/return and import-origin information the decomposer omits.

    Two products come out of a single walk: compact context lines that Plan Mode
    can inject before generation, and an on-demand diagram rendered from the real
    repository rather than a hand-drawn one.
    """

    name = "agent-repo-map"

    def __init__(self, skip_dirs: frozenset[str] | set[str] | None = None) -> None:
        self.skip_dirs = frozenset(skip_dirs) if skip_dirs is not None else DEFAULT_SKIP_DIRS
        self._decomposer = DecompositionEngine()

    # -- walking -----------------------------------------------------------------

    def map_repo(self, root: Path | str) -> RepoGraph:
        root_path = Path(root)
        local_tops = self._local_top_names(root_path)
        files: list[FileRecord] = []
        for path in self._iter_python_files(root_path):
            rel = path.relative_to(root_path).as_posix()
            module = self._module_name(path, root_path)
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                files.append(FileRecord(path=rel, module=module, parse_error=f"read error: {exc}"))
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError as exc:
                files.append(
                    FileRecord(path=rel, module=module, parse_error=f"syntax error: {exc.msg}")
                )
                continue
            files.append(self._build_file_record(rel, module, source, tree, local_tops))
        graph = RepoGraph(root=str(root_path), files=files, local_modules=sorted(local_tops))
        graph.nodes, graph.edges = self._build_graph(files)
        return graph

    def _iter_python_files(self, root: Path) -> list[Path]:
        collected: list[Path] = []
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in self.skip_dirs
                and not name.startswith(".")
                and not name.endswith(".egg-info")
            )
            for filename in sorted(filenames):
                if filename.endswith(".py"):
                    collected.append(Path(current) / filename)
        return collected

    def _local_top_names(self, root: Path) -> set[str]:
        local: set[str] = set()
        if not root.is_dir():
            return local
        for entry in root.iterdir():
            if entry.is_file() and entry.suffix == ".py" and entry.stem != "__init__":
                local.add(entry.stem)
            elif entry.is_dir() and entry.name not in self.skip_dirs and not entry.name.startswith("."):
                if any(child.suffix == ".py" for child in entry.rglob("*.py")):
                    local.add(entry.name)
        return local

    def _module_name(self, path: Path, root: Path) -> str:
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    # -- per-file extraction -----------------------------------------------------

    def _build_file_record(
        self,
        rel: str,
        module: str,
        source: str,
        tree: ast.AST,
        local_tops: set[str],
    ) -> FileRecord:
        functions: list[FunctionInfo] = []
        classes: list[str] = []
        module_vars: list[str] = []
        class_vars: list[str] = []
        instance_vars: list[str] = []
        imports: list[ImportInfo] = []
        seen_instance: set[str] = set()
        seen_module_vars: set[str] = set()

        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        ir = self._decomposer.decompose(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        ImportInfo(
                            module=alias.name,
                            kind=self._classify_import(alias.name, 0, local_tops),
                            line=node.lineno,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                imports.append(
                    ImportInfo(
                        module=node.module or "",
                        kind=self._classify_import(node.module or "", node.level, local_tops),
                        names=[alias.name for alias in node.names],
                        level=node.level,
                        line=node.lineno,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
                for statement in node.body:
                    for target in self._assignment_targets(statement):
                        if isinstance(target, ast.Name):
                            class_vars.append(f"{node.name}.{target.id}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info, function_instance_vars = self._function_info(node, parents)
                functions.append(info)
                for name in function_instance_vars:
                    if name not in seen_instance:
                        seen_instance.add(name)
                        instance_vars.append(name)

        for statement in getattr(tree, "body", []):
            for target in self._assignment_targets(statement):
                if isinstance(target, ast.Name) and target.id not in seen_module_vars:
                    seen_module_vars.add(target.id)
                    module_vars.append(target.id)

        return FileRecord(
            path=rel,
            module=module,
            functions=functions,
            classes=classes,
            module_vars=module_vars,
            class_vars=class_vars,
            instance_vars=instance_vars,
            variables=[
                VariableInfo(
                    name=symbol.name,
                    scope=".".join(symbol.scope_path),
                    kind=symbol.kind,
                    line=symbol.line,
                )
                for symbol in ir.symbols
            ],
            loops=[
                LoopSite(loop_type=loop.loop_type, depth=loop.depth, line=loop.line)
                for loop in ir.loops
            ],
            imports=imports,
            mutations=[
                MutationInfo(
                    target=mutation.target,
                    scope=mutation.scope,
                    mutation_type=mutation.mutation_type,
                    line=mutation.line,
                )
                for mutation in ir.mutations
            ],
        )

    def _function_info(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parents: dict[ast.AST, ast.AST],
    ) -> tuple[FunctionInfo, list[str]]:
        arguments = node.args
        args = [arg.arg for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)]
        if arguments.vararg is not None:
            args.append(f"*{arguments.vararg.arg}")
        if arguments.kwarg is not None:
            args.append(f"**{arguments.kwarg.arg}")

        calls: list[str] = []
        seen_calls: set[str] = set()
        returns_value = False
        instance_vars: list[str] = []
        variables = list(args)
        seen_variables = set(variables)
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = self._call_name(child.func)
                if name and name not in seen_calls:
                    seen_calls.add(name)
                    calls.append(name)
            elif isinstance(child, ast.Return) and child.value is not None:
                returns_value = True
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                for name in self._target_names(child.target):
                    if name not in seen_variables:
                        seen_variables.add(name)
                        variables.append(name)
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                for target in self._assignment_targets(child):
                    for name in self._target_names(target):
                        if name not in seen_variables:
                            seen_variables.add(name)
                            variables.append(name)
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        instance_vars.append(target.attr)

        if node.returns is not None:
            returns = self._annotation_text(node.returns)
        else:
            returns = "value" if returns_value else "none"
        return (
            FunctionInfo(
                name=node.name,
                qualified_name=self._qualified_function_name(node, parents),
                args=args,
                calls=calls,
                variables=variables,
                returns=returns,
                line=node.lineno,
            ),
            instance_vars,
        )

    def _assignment_targets(self, node: ast.AST) -> list[ast.expr]:
        if isinstance(node, ast.Assign):
            return list(node.targets)
        if isinstance(node, ast.AnnAssign):
            return [node.target]
        if isinstance(node, ast.AugAssign):
            return [node.target]
        return []

    def _target_names(self, node: ast.AST) -> list[str]:
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, ast.Attribute):
            return [self._call_name(node)]
        if isinstance(node, (ast.Tuple, ast.List)):
            return [name for item in node.elts for name in self._target_names(item)]
        return []

    def _qualified_function_name(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parents: dict[ast.AST, ast.AST],
    ) -> str:
        names = [node.name]
        parent = parents.get(node)
        while parent is not None:
            if isinstance(parent, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(parent.name)
            parent = parents.get(parent)
        return ".".join(reversed(names))

    def _call_name(self, func: ast.expr) -> str:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                return f"{func.value.id}.{func.attr}"
            return func.attr
        return ""

    def _annotation_text(self, node: ast.expr) -> str:
        try:
            return " ".join(ast.unparse(node).split())
        except Exception:  # noqa: BLE001 - annotation rendering is best-effort
            return "value"

    def _classify_import(self, module: str, level: int, local_tops: set[str]) -> str:
        if level and level > 0:
            return "local"
        if not module:
            return "local"
        top = module.split(".", 1)[0]
        if top in local_tops:
            return "local"
        if top in _STDLIB_NAMES:
            return "stdlib"
        return "third_party"

    # -- graph construction -----------------------------------------------------

    def _build_graph(self, files: list[FileRecord]) -> tuple[list[GraphNode], list[GraphEdge]]:
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        node_ids: set[str] = set()
        known_functions: dict[tuple[str, str], str] = {}
        known_modules = {
            record.module for record in files if not record.parse_error and record.module
        }

        def add_node(node: GraphNode) -> None:
            if node.id not in node_ids:
                node_ids.add(node.id)
                nodes.append(node)

        for record in files:
            if record.parse_error:
                continue
            module_id = self._node_id("module", record.module or record.path)
            add_node(GraphNode(id=module_id, kind="module", label=record.module or record.path, module=record.module))
            for function in record.functions:
                qualified = function.qualified_name or function.name
                function_id = self._node_id("function", f"{record.module}:{qualified}")
                known_functions[(record.module, function.name)] = function_id
                known_functions[(record.module, qualified)] = function_id
                add_node(
                    GraphNode(
                        id=function_id,
                        kind="function",
                        label=f"{record.module}.{qualified}",
                        module=record.module,
                        line=function.line,
                    )
                )
                edges.append(GraphEdge(source=module_id, target=function_id, kind="contains"))
            for variable in record.variables:
                variable_id = self._node_id(
                    "variable", f"{record.module}:{variable.scope}:{variable.name}"
                )
                add_node(
                    GraphNode(
                        id=variable_id,
                        kind="variable",
                        label=f"{variable.scope}.{variable.name}",
                        module=record.module,
                        line=variable.line,
                    )
                )
                edges.append(GraphEdge(source=module_id, target=variable_id, kind="declares"))
            for loop in record.loops:
                loop_id = self._node_id("loop", f"{record.module}:{loop.line}:{loop.depth}")
                add_node(
                    GraphNode(
                        id=loop_id,
                        kind="loop",
                        label=f"{loop.loop_type} loop depth {loop.depth}",
                        module=record.module,
                        line=loop.line,
                    )
                )
                edges.append(GraphEdge(source=module_id, target=loop_id, kind="contains"))

        for record in files:
            if record.parse_error:
                continue
            module_id = self._node_id("module", record.module or record.path)
            import_aliases: dict[str, str] = {}
            for imported in record.imports:
                target_name = self._resolve_import_module(record, imported, known_modules)
                if not target_name:
                    target_name = imported.module or "."
                target_id = self._node_id("module", target_name)
                add_node(
                    GraphNode(
                        id=target_id,
                        kind=f"{imported.kind}_module",
                        label=target_name,
                        module=target_name,
                        line=imported.line,
                    )
                )
                edges.append(
                    GraphEdge(
                        source=module_id,
                        target=target_id,
                        kind="imports",
                        label=imported.kind,
                        line=imported.line,
                    )
                )
                import_aliases[target_name.rsplit(".", 1)[-1]] = target_name
                for name in imported.names:
                    if not imported.module and imported.level > 0:
                        import_aliases[name] = target_name
                    else:
                        import_aliases[name] = (
                            f"{target_name}.{name}" if target_name != "." else name
                        )

            for function in record.functions:
                source_id = known_functions[(record.module, function.qualified_name or function.name)]
                for call in function.calls:
                    target_id = self._resolve_call_target(
                        record.module, call, import_aliases, known_functions
                    )
                    if target_id not in node_ids:
                        add_node(
                            GraphNode(
                                id=target_id,
                                kind="callable",
                                label=call,
                                module=record.module,
                            )
                        )
                    edges.append(
                        GraphEdge(
                            source=source_id,
                            target=target_id,
                            kind="calls",
                            label=call,
                            line=function.line,
                        )
                    )

            for mutation in record.mutations:
                source_id = known_functions.get(
                    (record.module, mutation.scope), module_id
                )
                target_id = self._node_id(
                    "variable", f"{record.module}:{mutation.scope}:{mutation.target}"
                )
                add_node(
                    GraphNode(
                        id=target_id,
                        kind="variable",
                        label=mutation.target,
                        module=record.module,
                        line=mutation.line,
                    )
                )
                edges.append(
                    GraphEdge(
                        source=source_id,
                        target=target_id,
                        kind="mutates",
                        label=mutation.mutation_type,
                        line=mutation.line,
                    )
                )
        return self._dedupe_nodes(nodes), self._dedupe_edges(edges)

    def _resolve_call_target(
        self,
        module: str,
        call: str,
        import_aliases: dict[str, str],
        known_functions: dict[tuple[str, str], str],
    ) -> str:
        if (module, call) in known_functions:
            return known_functions[(module, call)]
        head, _, tail = call.partition(".")
        imported = import_aliases.get(head, "")
        if imported:
            imported_module = imported if not tail else imported
            candidate = tail or imported.rsplit(".", 1)[-1]
            if (imported_module, candidate) in known_functions:
                return known_functions[(imported_module, candidate)]
            parent = imported_module.rsplit(".", 1)[0] if "." in imported_module else imported_module
            if (parent, candidate) in known_functions:
                return known_functions[(parent, candidate)]
        return self._node_id("callable", f"{module}:{call}")

    def _resolve_import_module(
        self,
        record: FileRecord,
        imported: ImportInfo,
        known_modules: set[str],
    ) -> str:
        """Resolve an import to the concrete local module when one exists."""

        if imported.level > 0:
            package_parts = record.module.split(".") if record.module else []
            if not record.path.endswith("/__init__.py") and record.path != "__init__.py":
                package_parts = package_parts[:-1]
            ascend = imported.level - 1
            if ascend:
                package_parts = package_parts[: max(0, len(package_parts) - ascend)]
            module_parts = imported.module.split(".") if imported.module else []
            base = ".".join([*package_parts, *module_parts])
        else:
            base = imported.module

        if base in known_modules:
            resolved_base = base
        else:
            resolved_base = ""
        for name in imported.names:
            candidate = f"{base}.{name}" if base else name
            if candidate in known_modules:
                return candidate
        return resolved_base or base

    def _node_id(self, kind: str, value: str) -> str:
        return f"{kind}:{value}"

    def _dedupe_nodes(self, nodes: list[GraphNode]) -> list[GraphNode]:
        return list({node.id: node for node in nodes}.values())

    def _dedupe_edges(self, edges: list[GraphEdge]) -> list[GraphEdge]:
        unique: dict[tuple[str, str, str, str, int], GraphEdge] = {}
        for edge in edges:
            unique[(edge.source, edge.target, edge.kind, edge.label, edge.line)] = edge
        return list(unique.values())

    # -- renderings --------------------------------------------------------------

    def to_plan_context(self, graph: RepoGraph) -> list[str]:
        """Compact, bounded lines suitable for Plan Mode graph context."""

        analyzed = [record for record in graph.files if not record.parse_error]
        if not analyzed:
            return []
        lines = [f"REPO MAP ({len(analyzed)} python files under {graph.root}):"]
        for record in analyzed[:_MAX_CONTEXT_FILES]:
            names = [function.name for function in record.functions]
            shown = ", ".join(names[:_MAX_CONTEXT_FUNCTIONS])
            if len(names) > _MAX_CONTEXT_FUNCTIONS:
                shown += f", (+{len(names) - _MAX_CONTEXT_FUNCTIONS} more)"
            summary = f"- {record.module or record.path}: defs [{shown}]" if shown else f"- {record.module or record.path}: defs []"
            local_imports = [imp.module for imp in record.imports if imp.kind == "local" and imp.module]
            if local_imports:
                summary += f"; local_imports [{', '.join(sorted(set(local_imports))[:_MAX_CONTEXT_IMPORTS])}]"
            third_party = [imp.module for imp in record.imports if imp.kind == "third_party" and imp.module]
            if third_party:
                summary += f"; third_party [{', '.join(sorted(set(third_party))[:_MAX_CONTEXT_IMPORTS])}]"
            if record.max_loop_depth > 1:
                summary += f"; max_loop_depth {record.max_loop_depth}"
            lines.append(summary)
        if len(analyzed) > _MAX_CONTEXT_FILES:
            lines.append(f"- (+{len(analyzed) - _MAX_CONTEXT_FILES} more files omitted from compact context)")
        call_edges = [edge for edge in graph.edges if edge.kind == "calls"]
        if call_edges:
            lines.append("Call edges:")
            for edge in call_edges[:_MAX_CONTEXT_EDGES]:
                lines.append(f"- {self._node_label(graph, edge.source)} -> {self._node_label(graph, edge.target)}")
        import_edges = [edge for edge in graph.edges if edge.kind == "imports"]
        if import_edges:
            lines.append("Import edges:")
            for edge in import_edges[:_MAX_CONTEXT_EDGES]:
                lines.append(
                    f"- {self._node_label(graph, edge.source)} imports "
                    f"{self._node_label(graph, edge.target)} ({edge.label})"
                )
        return lines

    def to_mermaid(self, graph: RepoGraph) -> str:
        lines = ["flowchart LR"]
        mermaid_ids = {node.id: f"n{index}" for index, node in enumerate(graph.nodes)}
        for node in graph.nodes:
            label = node.label.replace('"', "'")
            lines.append(f'  {mermaid_ids[node.id]}["{node.kind}: {label}"]')
        for edge in graph.edges:
            if edge.source in mermaid_ids and edge.target in mermaid_ids:
                label = edge.kind if not edge.label else f"{edge.kind}: {edge.label}"
                lines.append(
                    f"  {mermaid_ids[edge.source]} -->|{label.replace('|', '/')}| "
                    f"{mermaid_ids[edge.target]}"
                )
        return "\n".join(lines)

    def _node_label(self, graph: RepoGraph, node_id: str) -> str:
        for node in graph.nodes:
            if node.id == node_id:
                return node.label
        return node_id

    def _import_edges(self, graph: RepoGraph) -> list[tuple[str, str]]:
        known_modules = {record.module for record in graph.files if not record.parse_error and record.module}
        edges: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for record in graph.files:
            if record.parse_error or not record.module:
                continue
            for imp in record.imports:
                if imp.kind != "local":
                    continue
                # Prefer a `from package import submodule` target so the edge points
                # at the concrete dependency rather than the package __init__.
                target = self._resolve_local_module(record, imp, known_modules) or (
                    imp.module if imp.module in known_modules else ""
                )
                if not target or target == record.module:
                    continue
                edge = (record.module, target)
                if edge not in seen:
                    seen.add(edge)
                    edges.append(edge)
        return edges

    def _resolve_local_module(
        self,
        record: FileRecord,
        imp: ImportInfo,
        known_modules: set[str],
    ) -> str:
        return self._resolve_import_module(record, imp, known_modules)

    def run(self, root: Path | str) -> AgentResult:
        graph = self.map_repo(root)
        return AgentResult(
            agent=self.name,
            payload={
                "root": graph.root,
                "file_count": len([record for record in graph.files if not record.parse_error]),
                "skipped": [record.path for record in graph.files if record.parse_error],
                "local_modules": graph.local_modules,
                "plan_context": self.to_plan_context(graph),
                "graph": asdict(graph),
            },
        )
