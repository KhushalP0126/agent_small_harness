from __future__ import annotations

import ast
from dataclasses import dataclass, field


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
class StructuralIR:
    functions: list[FunctionRecord] = field(default_factory=list)
    loops: list[LoopRecord] = field(default_factory=list)
    branches: list[BranchRecord] = field(default_factory=list)
    mutations: list[MutationRecord] = field(default_factory=list)
    explicit_globals: list[str] = field(default_factory=list)
    module_state_names: list[str] = field(default_factory=list)
    loop_mutation_targets: list[str] = field(default_factory=list)


class _IRBuilder(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.ir = StructuralIR()
        self.scope_stack = ["module"]
        self.loop_depth = 0
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

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.ir.functions.append(FunctionRecord(name=node.name, line=node.lineno))
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_For(self, node: ast.For) -> None:
        self.loop_depth += 1
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

    def visit_While(self, node: ast.While) -> None:
        self.loop_depth += 1
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
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self.scope_stack[-1] == "module" and isinstance(node.value, (ast.List, ast.Dict, ast.Set)):
            if isinstance(node.target, ast.Name):
                self.ir.module_state_names.append(node.target.id)
        if isinstance(node.target, ast.Subscript):
            self._record_mutation(node, "subscript_assign", self._target_text(node.target.value))
        elif isinstance(node.target, ast.Name):
            self._record_mutation(node, "assign", node.target.id)
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


class DecompositionEngine:
    name = "engine-0-decomposition"

    def decompose(self, source: str) -> StructuralIR:
        tree = ast.parse(source)
        builder = _IRBuilder(source)
        builder.visit(tree)
        builder.ir.explicit_globals = sorted(set(builder.ir.explicit_globals))
        builder.ir.module_state_names = sorted(set(builder.ir.module_state_names))
        builder.ir.loop_mutation_targets = sorted(builder.loop_mutation_targets)
        return builder.ir
