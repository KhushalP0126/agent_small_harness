from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass, field
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
    args: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
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
class FileRecord:
    path: str
    module: str
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    module_vars: list[str] = field(default_factory=list)
    instance_vars: list[str] = field(default_factory=list)
    loops: list[LoopSite] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    parse_error: str = ""

    @property
    def max_loop_depth(self) -> int:
        return max((loop.depth for loop in self.loops), default=0)


@dataclass
class RepoGraph:
    root: str
    files: list[FileRecord] = field(default_factory=list)
    local_modules: list[str] = field(default_factory=list)


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
        return RepoGraph(root=str(root_path), files=files, local_modules=sorted(local_tops))

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
        instance_vars: list[str] = []
        imports: list[ImportInfo] = []
        seen_instance: set[str] = set()
        seen_module_vars: set[str] = set()

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
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info, function_instance_vars = self._function_info(node)
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
            instance_vars=instance_vars,
            loops=self._loop_sites(source),
            imports=imports,
        )

    def _function_info(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[FunctionInfo, list[str]]:
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
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = self._call_name(child.func)
                if name and name not in seen_calls:
                    seen_calls.add(name)
                    calls.append(name)
            elif isinstance(child, ast.Return) and child.value is not None:
                returns_value = True
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                for target in self._assignment_targets(child):
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
            FunctionInfo(name=node.name, args=args, calls=calls, returns=returns, line=node.lineno),
            instance_vars,
        )

    def _loop_sites(self, source: str) -> list[LoopSite]:
        try:
            ir = self._decomposer.decompose(source)
        except SyntaxError:
            return []
        return [LoopSite(loop_type=loop.loop_type, depth=loop.depth, line=loop.line) for loop in ir.loops]

    def _assignment_targets(self, node: ast.AST) -> list[ast.expr]:
        if isinstance(node, ast.Assign):
            return list(node.targets)
        if isinstance(node, ast.AnnAssign):
            return [node.target]
        return []

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
        edges = self._import_edges(graph)
        if edges:
            lines.append("Local import edges:")
            for source_module, target_module in edges[:_MAX_CONTEXT_EDGES]:
                lines.append(f"- {source_module} -> {target_module}")
        return lines

    def to_mermaid(self, graph: RepoGraph) -> str:
        analyzed = [record for record in graph.files if not record.parse_error]
        module_ids = {record.module: f"m{index}" for index, record in enumerate(analyzed)}
        lines = ["flowchart LR"]
        for record in analyzed:
            node_id = module_ids[record.module]
            label = record.module or record.path
            function_names = ", ".join(function.name for function in record.functions[:_MAX_CONTEXT_FUNCTIONS])
            caption = f"{label}<br/>{function_names}" if function_names else label
            lines.append(f'  {node_id}["{caption}"]')
        for source_module, target_module in self._import_edges(graph):
            if source_module in module_ids and target_module in module_ids:
                lines.append(f"  {module_ids[source_module]} --> {module_ids[target_module]}")
        return "\n".join(lines)

    def _import_edges(self, graph: RepoGraph) -> list[tuple[str, str]]:
        known_modules = {record.module for record in graph.files if not record.parse_error and record.module}
        edges: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for record in graph.files:
            if record.parse_error or not record.module:
                continue
            for imp in record.imports:
                if imp.kind != "local" or not imp.module:
                    continue
                # Prefer a `from package import submodule` target so the edge points
                # at the concrete dependency rather than the package __init__.
                target = self._resolve_local_module(imp, known_modules) or (
                    imp.module if imp.module in known_modules else ""
                )
                if not target or target == record.module:
                    continue
                edge = (record.module, target)
                if edge not in seen:
                    seen.add(edge)
                    edges.append(edge)
        return edges

    def _resolve_local_module(self, imp: ImportInfo, known_modules: set[str]) -> str:
        # ``from package import symbol`` where ``package.symbol`` is itself a module.
        for name in imp.names:
            candidate = f"{imp.module}.{name}" if imp.module else name
            if candidate in known_modules:
                return candidate
        return ""

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
            },
        )
