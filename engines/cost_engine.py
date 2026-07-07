from __future__ import annotations

import ast
from dataclasses import dataclass

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding


@dataclass
class MembershipHotspot:
    container: str
    line: int


class ContainerTypeTracker:
    def __init__(self) -> None:
        self._scopes: list[dict[str, str]] = [{}]

    def push_scope(self) -> None:
        self._scopes.append({})

    def pop_scope(self) -> None:
        if len(self._scopes) == 1:
            return
        self._scopes.pop()

    def record(self, name: str, kind: str) -> None:
        if not name:
            return
        self._scopes[-1][name] = kind

    def kind_of(self, name: str) -> str:
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return ""


class _CostVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.loop_depth = 0
        self.tracker = ContainerTypeTracker()
        self.hotspots: list[MembershipHotspot] = []

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
        if annotation in {"list", "List", "tuple", "Tuple", "Sequence", "MutableSequence"}:
            return "list"
        if annotation in {"str", "String"}:
            return "str"
        return ""

    def _record_name_type(self, name: str, kind: str) -> None:
        self.tracker.record(name, kind)

    def _infer_container_kind(self, node: ast.AST) -> str:
        if isinstance(node, ast.Dict):
            return "dict"
        if isinstance(node, (ast.Set, ast.SetComp)):
            return "set"
        if isinstance(node, (ast.List, ast.ListComp, ast.Tuple)):
            return "list"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return "str"
        if isinstance(node, ast.JoinedStr):
            return "str"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"dict", "set", "list", "tuple", "str"}:
                return node.func.id
        return ""

    def _record_arguments(self, arguments: ast.arguments) -> None:
        for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
            annotation = self._annotation_name(arg.annotation)
            self._record_name_type(arg.arg, self._kind_from_annotation(annotation))
        if arguments.vararg is not None:
            self._record_name_type(arguments.vararg.arg, "")
        if arguments.kwarg is not None:
            self._record_name_type(arguments.kwarg.arg, "")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)
        self.tracker.push_scope()
        self._record_arguments(node.args)
        for statement in node.body:
            self.visit(statement)
        self.tracker.pop_scope()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        self.tracker.push_scope()
        self._record_arguments(node.args)
        self.visit(node.body)
        self.tracker.pop_scope()

    def visit_Assign(self, node: ast.Assign) -> None:
        kind = self._infer_container_kind(node.value)
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            self._record_name_type(target.id, kind)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            kind = self._infer_container_kind(node.value) if node.value is not None else ""
            if not kind:
                annotation = self._annotation_name(node.annotation)
                kind = self._kind_from_annotation(annotation)
            self._record_name_type(node.target.id, kind)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1

    def visit_While(self, node: ast.While) -> None:
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self.loop_depth += len(node.generators)
        self.generic_visit(node)
        self.loop_depth -= len(node.generators)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self.loop_depth += len(node.generators)
        self.generic_visit(node)
        self.loop_depth -= len(node.generators)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.loop_depth += len(node.generators)
        self.generic_visit(node)
        self.loop_depth -= len(node.generators)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.loop_depth += len(node.generators)
        self.generic_visit(node)
        self.loop_depth -= len(node.generators)

    def visit_Compare(self, node: ast.Compare) -> None:
        if self.loop_depth:
            for operator, comparator in zip(node.ops, node.comparators):
                if isinstance(operator, (ast.In, ast.NotIn)) and isinstance(comparator, ast.Name):
                    if self.tracker.kind_of(comparator.id) == "list":
                        self.hotspots.append(
                            MembershipHotspot(
                                container=comparator.id,
                                line=getattr(node, "lineno", 0),
                            )
                        )
        self.generic_visit(node)


class CostEngine(BaseEngine):
    name = "engine-4-cost"

    def scan(self, source: str) -> list[EngineFinding]:
        tree = ast.parse(source)
        visitor = _CostVisitor()
        visitor.visit(tree)
        if visitor.hotspots:
            containers = sorted({hotspot.container for hotspot in visitor.hotspots})
            lines = sorted({hotspot.line for hotspot in visitor.hotspots})
            return [
                EngineFinding(
                    engine=self.name,
                    severity="High",
                    summary="Linear membership test inside loop",
                    details=(
                        "A loop performs membership checks against a non-set container. "
                        "This often creates O(N*M) behavior and should usually be converted to set lookup."
                    ),
                    metrics={
                        "containers": containers,
                        "lines": lines,
                        "hotspot_count": len(visitor.hotspots),
                    },
                    diagnostic=EngineDiagnostic(
                        violation="ALGORITHMIC_COST",
                        threshold="avoid repeated linear membership inside loops",
                        actual=", ".join(containers),
                        location=", ".join(f"line {line}" for line in lines),
                        recommended_refactor=(
                            "Precompute a set for repeated membership checks before the loop, then test against that set."
                        ),
                    ),
                )
            ]
        return [
            EngineFinding(
                engine=self.name,
                severity="Low",
                summary="No repeated linear membership hotspot detected",
                details="No loop membership checks against non-set containers were found.",
                metrics={"hotspot_count": 0},
            )
        ]
