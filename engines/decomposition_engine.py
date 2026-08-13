from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field

_STDLIB_ROOTS = set(getattr(sys, "stdlib_module_names", set())) | {"__future__"}


STATE_NAME_HINTS = (
    "state",
    "section",
    "current",
    "context",
    "ctx",
    "total",
    "balance",
)


@dataclass
class LoopRecord:
    loop_type: str
    depth: int
    target: str
    line: int
    path: list[str] = field(default_factory=list)


@dataclass
class BranchRecord:
    branch_type: str
    condition: str
    line: int


@dataclass
class MutationRecord:
    mutation_type: str
    target: str
    line: int
    scope: str


@dataclass
class FunctionRecord:
    name: str
    line: int


@dataclass
class SymbolRecord:
    name: str
    kind: str
    scope_path: tuple[str, ...]
    line: int
    inferred_from: str = "unknown"


@dataclass
class ImportRecord:
    """Language-agnostic import/dependency record.

    For Python:
    - ``name`` is the imported module path (``pygame``, ``pygame.draw``, or a
      relative module marker).
    - ``bound_symbols`` are local names introduced by the statement.
    - ``bound_paths`` parallel ``bound_symbols`` with the registered-library API
      prefix used for allow-list checks (empty for plain ``import mod``;
      ``draw.rect`` for ``from pygame.draw import rect as r``).
    - ``is_stdlib`` marks standard-library roots (relative imports are never stdlib).
    - ``relative_level`` is the ``from .x import ...`` level (0 for absolute).
    """

    name: str
    language: str = "python"
    kind: str = "module"  # module | header | crate | require
    line: int = 0
    bound_symbols: list[str] = field(default_factory=list)
    bound_paths: list[str] = field(default_factory=list)
    is_stdlib: bool = False
    relative_level: int = 0


@dataclass
class MembershipCheck:
    container: str
    scope_path: tuple[str, ...]
    line: int
    operator: str


@dataclass
class BoundsRiskRecord:
    summary: str
    line: int
    expression: str


@dataclass
class StateFlowRiskRecord:
    function_name: str
    line: int
    parameter: str


@dataclass
class StructuralIR:
    functions: list[FunctionRecord] = field(default_factory=list)
    loops: list[LoopRecord] = field(default_factory=list)
    branches: list[BranchRecord] = field(default_factory=list)
    mutations: list[MutationRecord] = field(default_factory=list)
    explicit_globals: list[str] = field(default_factory=list)
    module_state_names: list[str] = field(default_factory=list)
    loop_mutation_targets: list[str] = field(default_factory=list)
    symbols: list[SymbolRecord] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    membership_checks: list[MembershipCheck] = field(default_factory=list)
    bounds_risks: list[BoundsRiskRecord] = field(default_factory=list)
    state_flow_risks: list[StateFlowRiskRecord] = field(default_factory=list)

    def resolve_symbol(
        self,
        name: str,
        scope_path: tuple[str, ...],
        line: int | None = None,
    ) -> SymbolRecord | None:
        """Resolve the nearest visible symbol, preserving Python-like shadowing."""
        for end in range(len(scope_path), -1, -1):
            candidate_scope = scope_path[:end]
            candidates = [
                symbol
                for symbol in self.symbols
                if symbol.name == name
                and symbol.scope_path == candidate_scope
                and (line is None or symbol.line <= line)
            ]
            if candidates:
                return max(candidates, key=lambda symbol: symbol.line)
        return None

    def kind_of(self, name: str, scope_path: tuple[str, ...], line: int | None = None) -> str:
        symbol = self.resolve_symbol(name, scope_path, line)
        return symbol.kind if symbol else ""


class _IRBuilder(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.ir = StructuralIR()
        self.scope_stack = ["module"]
        self.loop_depth = 0
        self.membership_loop_depth = 0
        self.loop_mutation_targets: set[str] = set()
        self.loop_path_stack: list[str] = []

    def _expr_text(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        text = ast.get_source_segment(self.source, node)
        if text:
            return " ".join(text.split())
        if isinstance(node, ast.Name):
            return node.id
        return node.__class__.__name__

    def _target_text(self, node: ast.AST) -> str:
        text = ast.get_source_segment(self.source, node)
        if text:
            return " ".join(text.split())
        if isinstance(node, ast.Name):
            return node.id
        return node.__class__.__name__

    def _segment(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.source, node) or type(node).__name__

    def _record_mutation(self, node: ast.AST, mutation_type: str, target: str) -> None:
        self.ir.mutations.append(
            MutationRecord(
                mutation_type=mutation_type,
                target=target,
                line=getattr(node, "lineno", 0),
                scope=self.scope_stack[-1],
            )
        )
        if self.loop_depth > 0:
            self.loop_mutation_targets.add(target)

    def _annotation_name(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript):
            return self._annotation_name(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value.split("[", 1)[0]
        return ""

    def _kind_from_annotation(self, annotation: str) -> str:
        if annotation in {"dict", "Dict", "Mapping", "MutableMapping"}:
            return "dict"
        if annotation in {"set", "Set", "frozenset", "FrozenSet"}:
            return "set"
        if annotation in {"tuple", "Tuple"}:
            return "tuple"
        if annotation in {"list", "List", "Sequence", "MutableSequence"}:
            return "list"
        if annotation in {"str", "String"}:
            return "str"
        return "unknown"

    def _infer_container_kind(self, node: ast.AST | None) -> str:
        if isinstance(node, ast.Dict):
            return "dict"
        if isinstance(node, (ast.Set, ast.SetComp)):
            return "set"
        if isinstance(node, (ast.List, ast.ListComp)):
            return "list"
        if isinstance(node, ast.Tuple):
            return "tuple"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return "str"
        if isinstance(node, ast.JoinedStr):
            return "str"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"dict", "set", "list", "tuple", "str"}:
                return node.func.id
        return "unknown"

    def _record_symbol(self, node: ast.AST, name: str, kind: str, inferred_from: str) -> None:
        self.ir.symbols.append(
            SymbolRecord(
                name=name,
                kind=kind or "unknown",
                scope_path=tuple(self.scope_stack[1:]),
                line=getattr(node, "lineno", 0),
                inferred_from=inferred_from,
            )
        )

    def _record_arguments(self, arguments: ast.arguments) -> None:
        all_args = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        for arg in all_args:
            annotation = self._annotation_name(arg.annotation)
            self._record_symbol(arg, arg.arg, self._kind_from_annotation(annotation), "annotation")
        if arguments.vararg is not None:
            self._record_symbol(arguments.vararg, arguments.vararg.arg, "unknown", "argument")
        if arguments.kwarg is not None:
            self._record_symbol(arguments.kwarg, arguments.kwarg.arg, "unknown", "argument")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.ir.functions.append(FunctionRecord(name=node.name, line=node.lineno))
        self.scope_stack.append(node.name)
        self._record_arguments(node.args)
        self._record_state_flow_risks(node)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_While(self, node: ast.While) -> None:
        self.loop_depth += 1
        self.membership_loop_depth += 1
        loop_label = f"while:{self._expr_text(node.test)}"
        self.loop_path_stack.append(loop_label)
        self.ir.loops.append(
            LoopRecord(
                loop_type="while",
                depth=self.loop_depth,
                target=self._expr_text(node.test),
                line=node.lineno,
                path=list(self.loop_path_stack),
            )
        )
        self.generic_visit(node)
        self.loop_path_stack.pop()
        self.loop_depth -= 1
        self.membership_loop_depth -= 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def _visit_comprehension_scope(self, node: ast.AST, generators: list[ast.comprehension]) -> None:
        self.membership_loop_depth += len(generators)
        self.generic_visit(node)
        self.membership_loop_depth -= len(generators)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension_scope(node, node.generators)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension_scope(node, node.generators)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension_scope(node, node.generators)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension_scope(node, node.generators)

    def visit_If(self, node: ast.If) -> None:
        self.ir.branches.append(
            BranchRecord(
                branch_type="if",
                condition=self._expr_text(node.test),
                line=node.lineno,
            )
        )
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.ir.explicit_globals.extend(node.names)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            local = alias.asname or root
            self.ir.imports.append(
                ImportRecord(
                    name=alias.name,
                    language="python",
                    kind="module",
                    line=getattr(node, "lineno", 0),
                    bound_symbols=[local],
                    bound_paths=[""],
                    is_stdlib=root in _STDLIB_ROOTS,
                    relative_level=0,
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        level = int(node.level or 0)
        if level:
            module_name = "." * level + (node.module or "")
            root = ""
            is_stdlib = False
            imported_path: list[str] = []
        else:
            module_name = node.module or ""
            root = module_name.split(".", 1)[0] if module_name else ""
            is_stdlib = bool(root) and root in _STDLIB_ROOTS
            imported_path = module_name.split(".")[1:] if module_name else []
        bound_symbols: list[str] = []
        bound_paths: list[str] = []
        for alias in node.names:
            local_name = alias.asname or alias.name
            bound_symbols.append(local_name)
            # Preserve original imported symbol name for allow-list prefixes.
            bound_paths.append(".".join([*imported_path, alias.name]) if not level else alias.name)
        self.ir.imports.append(
            ImportRecord(
                name=module_name or ".",
                language="python",
                kind="module",
                line=getattr(node, "lineno", 0),
                bound_symbols=bound_symbols,
                bound_paths=bound_paths,
                is_stdlib=is_stdlib,
                relative_level=level,
            )
        )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.scope_stack[-1] == "module" and isinstance(node.value, (ast.List, ast.Dict, ast.Set)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.ir.module_state_names.append(target.id)
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                self._record_mutation(node, "subscript_assign", self._target_text(target.value))
            elif isinstance(target, ast.Name):
                self._record_mutation(node, "assign", target.id)
                self._record_symbol(
                    node,
                    target.id,
                    self._infer_container_kind(node.value),
                    self._inference_source(node.value),
                )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self.scope_stack[-1] == "module" and isinstance(node.value, (ast.List, ast.Dict, ast.Set)):
            if isinstance(node.target, ast.Name):
                self.ir.module_state_names.append(node.target.id)
        if isinstance(node.target, ast.Subscript):
            self._record_mutation(node, "subscript_assign", self._target_text(node.target.value))
        elif isinstance(node.target, ast.Name):
            self._record_mutation(node, "assign", node.target.id)
            kind = self._infer_container_kind(node.value)
            if kind == "unknown":
                kind = self._kind_from_annotation(self._annotation_name(node.annotation))
            self._record_symbol(
                node,
                node.target.id,
                kind,
                self._inference_source(node.value) if node.value is not None else "annotation",
            )
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Subscript):
            self._record_mutation(node, "subscript_augassign", self._target_text(node.target.value))
        elif isinstance(node.target, ast.Name):
            self._record_mutation(node, "augassign", node.target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            self._record_mutation(node, f"call.{node.func.attr}", node.func.value.id)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if self.membership_loop_depth:
            for operator, comparator in zip(node.ops, node.comparators):
                if isinstance(operator, (ast.In, ast.NotIn)) and isinstance(comparator, ast.Name):
                    self.ir.membership_checks.append(
                        MembershipCheck(
                            container=comparator.id,
                            scope_path=tuple(self.scope_stack[1:]),
                            line=getattr(node, "lineno", 0),
                            operator="in" if isinstance(operator, ast.In) else "not in",
                        )
                    )
        self.generic_visit(node)

    def _inference_source(self, node: ast.AST | None) -> str:
        if isinstance(node, (ast.List, ast.ListComp, ast.Dict, ast.Set, ast.SetComp, ast.Tuple)):
            return "literal"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
            "dict",
            "set",
            "list",
            "tuple",
            "str",
        }:
            return "constructor"
        return "assignment"

    def _len_target(self, node: ast.AST) -> str:
        if not isinstance(node, ast.Call):
            return ""
        if not isinstance(node.func, ast.Name) or node.func.id != "len" or len(node.args) != 1:
            return ""
        return node.args[0].id if isinstance(node.args[0], ast.Name) else ""

    def _is_len_of(self, node: ast.AST, container: str) -> bool:
        return bool(container and self._len_target(node) == container)

    def _index_is_len_or_beyond(self, index: ast.AST, container: str) -> bool:
        if self._is_len_of(index, container):
            return True
        return (
            isinstance(index, ast.BinOp)
            and isinstance(index.op, ast.Add)
            and self._is_len_of(index.left, container)
        )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        container = node.value.id if isinstance(node.value, ast.Name) else ""
        if container and self._index_is_len_or_beyond(node.slice, container):
            mode = "write" if isinstance(node.ctx, ast.Store) else "read"
            self.ir.bounds_risks.append(
                BoundsRiskRecord(
                    summary=f"Potential out-of-bounds {mode}",
                    line=getattr(node, "lineno", 0),
                    expression=self._segment(node),
                )
            )
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._check_range_len_plus_one(node.iter, node.lineno)
        self.loop_depth += 1
        self.membership_loop_depth += 1
        loop_label = f"for:{self._target_text(node.target)}"
        self.loop_path_stack.append(loop_label)
        self.ir.loops.append(
            LoopRecord(
                loop_type="for",
                depth=self.loop_depth,
                target=self._target_text(node.target),
                line=node.lineno,
                path=list(self.loop_path_stack),
            )
        )
        self.generic_visit(node)
        self.loop_path_stack.pop()
        self.loop_depth -= 1
        self.membership_loop_depth -= 1

    def _check_range_len_plus_one(self, node: ast.AST, line: int) -> None:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "range":
            return
        if len(node.args) not in {1, 2}:
            return
        stop = node.args[0] if len(node.args) == 1 else node.args[1]
        if not isinstance(stop, ast.BinOp) or not isinstance(stop.op, ast.Add):
            return
        if not isinstance(stop.right, ast.Constant) or stop.right.value != 1:
            return
        if self._len_target(stop.left):
            self.ir.bounds_risks.append(
                BoundsRiskRecord(
                    summary="Potential range upper-bound overflow",
                    line=line,
                    expression=self._segment(node),
                )
            )

    def _record_state_flow_risks(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parameter_names = {arg.arg for arg in node.args.args}
        state_parameters = {
            name for name in parameter_names if any(hint in name.lower() for hint in STATE_NAME_HINTS)
        }
        if not state_parameters:
            return
        assigned: set[str] = set()
        returns_value = False
        returned_names: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store) and child.id in state_parameters:
                assigned.add(child.id)
            elif isinstance(child, ast.Return):
                if child.value is not None:
                    returns_value = True
                if isinstance(child.value, ast.Name):
                    returned_names.add(child.value.id)
                elif isinstance(child.value, (ast.Tuple, ast.List)):
                    returned_names.update(item.id for item in child.value.elts if isinstance(item, ast.Name))
        if not assigned:
            return
        lost = assigned if not returns_value else assigned - returned_names
        for parameter in sorted(lost):
            self.ir.state_flow_risks.append(
                StateFlowRiskRecord(node.name, getattr(node, "lineno", 0), parameter)
            )


class DecompositionEngine:
    name = "engine-0-decomposition"

    def decompose(self, source: str) -> StructuralIR:
        """Build StructuralIR in a single Python AST pass, including imports."""
        tree = ast.parse(source)
        builder = _IRBuilder(source)
        builder.visit(tree)
        builder.ir.explicit_globals = sorted(set(builder.ir.explicit_globals))
        builder.ir.module_state_names = sorted(set(builder.ir.module_state_names))
        builder.ir.loop_mutation_targets = sorted(builder.loop_mutation_targets)
        return builder.ir
