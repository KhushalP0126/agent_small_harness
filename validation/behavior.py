from __future__ import annotations

import ast
import multiprocessing as mp
import queue
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_BEHAVIOR_TIMEOUT_SECONDS = 1.0
BLOCKED_CALL_NAMES = {"__import__", "compile", "eval", "exec", "input", "open"}
BLOCKED_NODES = (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.Import, ast.ImportFrom)
SAFE_BUILTINS = {
    "__build_class__": __build_class__,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "classmethod": classmethod,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "range": range,
    "set": set,
    "object": object,
    "property": property,
    "sorted": sorted,
    "staticmethod": staticmethod,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "ValueError": ValueError,
}


@dataclass(frozen=True)
class BehaviorCase:
    name: str
    args: tuple[Any, ...]
    expected: Any
    kwargs: dict[str, Any] = field(default_factory=dict)
    setup_args: tuple[Any, ...] = field(default_factory=tuple)
    setup_kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FunctionBehaviorSpec:
    function_name: str
    cases: list[BehaviorCase]


@dataclass
class BehaviorIssue:
    case: str
    expected: str
    actual: str
    details: str


@dataclass
class BehaviorResult:
    is_compliant: bool
    issues: list[BehaviorIssue] = field(default_factory=list)


def format_behavior_spec(spec: FunctionBehaviorSpec) -> str:
    lines = [
        "Behavioral Unit Test Specification:",
        f"- Function under test: {spec.function_name}",
    ]
    for case in spec.cases:
        args = ", ".join(repr(arg) for arg in case.args)
        kwargs = ", ".join(f"{key}={value!r}" for key, value in case.kwargs.items())
        call_args = ", ".join(item for item in [args, kwargs] if item)
        if "." in spec.function_name:
            class_name, method_name = spec.function_name.split(".", 1)
            setup_args = ", ".join(repr(arg) for arg in case.setup_args)
            setup_kwargs = ", ".join(f"{key}={value!r}" for key, value in case.setup_kwargs.items())
            setup_call_args = ", ".join(item for item in [setup_args, setup_kwargs] if item)
            call = f"{class_name}({setup_call_args}).{method_name}({call_args})"
        else:
            call = f"{spec.function_name}({call_args})"
        lines.append(f"- {call} == {case.expected!r}  # {case.name}")
    return "\n".join(lines)


def serialize_behavior_result(result: BehaviorResult) -> dict[str, Any]:
    return {
        "is_compliant": result.is_compliant,
        "issues": [asdict(issue) for issue in result.issues],
    }


def mixed_hard_case_spec() -> FunctionBehaviorSpec:
    return FunctionBehaviorSpec(
        function_name="analyze",
        cases=[
            BehaviorCase(name="empty matrix", args=([],), expected=0),
            BehaviorCase(name="skips empty rows", args=([[], []],), expected=0),
            BehaviorCase(name="covers all value classes", args=([[], [-1, 0, 4, 10, 99, 100]],), expected=19),
            BehaviorCase(name="mixed rows", args=([[1, 2, 3], [10, 0, -5]],), expected=16),
        ],
    )


def validate_function_behavior(
    source: str,
    spec: FunctionBehaviorSpec,
    timeout_seconds: float = DEFAULT_BEHAVIOR_TIMEOUT_SECONDS,
) -> BehaviorResult:
    ctx = _multiprocessing_context()
    result_queue = ctx.Queue()
    process = ctx.Process(target=_behavior_worker, args=(source, spec, result_queue))
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join()
        return BehaviorResult(
            is_compliant=False,
            issues=[
                BehaviorIssue(
                    case="timeout",
                    expected=f"complete within {timeout_seconds:g}s",
                    actual="timeout",
                    details="Behavior validation exceeded the sandbox timeout.",
                )
            ],
        )
    try:
        payload = result_queue.get_nowait()
    except queue.Empty:
        return BehaviorResult(
            is_compliant=False,
            issues=[
                BehaviorIssue(
                    case="load",
                    expected="behavior result",
                    actual=f"process exit {process.exitcode}",
                    details="Behavior sandbox exited without returning a result.",
                )
            ],
        )
    return _deserialize_behavior_result(payload)


def _multiprocessing_context() -> mp.context.BaseContext:
    if "fork" in mp.get_all_start_methods():
        return mp.get_context("fork")
    return mp.get_context()


def _behavior_worker(source: str, spec: FunctionBehaviorSpec, result_queue: Any) -> None:
    result_queue.put(serialize_behavior_result(_validate_function_behavior_inline(source, spec)))


def _deserialize_behavior_result(payload: dict[str, Any]) -> BehaviorResult:
    return BehaviorResult(
        is_compliant=payload["is_compliant"],
        issues=[BehaviorIssue(**issue) for issue in payload["issues"]],
    )


def _validate_function_behavior_inline(source: str, spec: FunctionBehaviorSpec) -> BehaviorResult:
    issues: list[BehaviorIssue] = []
    try:
        tree = ast.parse(source)
        _validate_runtime_ast(tree)
        tree = _strip_annotations(tree)
        namespace: dict[str, Any] = {"__builtins__": SAFE_BUILTINS, "__name__": "__behavior_check__"}
        exec(compile(tree, "<behavior-check>", "exec"), namespace)
    except Exception as exc:
        return BehaviorResult(
            is_compliant=False,
            issues=[
                BehaviorIssue(
                    case="load",
                    expected="valid executable Python",
                    actual=exc.__class__.__name__,
                    details=str(exc),
                )
            ],
        )

    candidate_issue = _candidate_lookup_issue(namespace, spec.function_name)
    if candidate_issue is not None:
        return BehaviorResult(is_compliant=False, issues=[candidate_issue])

    for case in spec.cases:
        try:
            candidate = _resolve_case_callable(namespace, spec.function_name, case)
            actual = candidate(*deepcopy(case.args), **deepcopy(case.kwargs))
        except Exception as exc:
            issues.append(
                BehaviorIssue(
                    case=case.name,
                    expected=repr(case.expected),
                    actual=exc.__class__.__name__,
                    details=str(exc),
                )
            )
            continue
        if actual != case.expected:
            issues.append(
                BehaviorIssue(
                    case=case.name,
                    expected=repr(case.expected),
                    actual=repr(actual),
                    details="Return value did not match the behavior spec.",
                )
            )

    return BehaviorResult(is_compliant=not issues, issues=issues)


def _candidate_lookup_issue(namespace: dict[str, Any], target_name: str) -> BehaviorIssue | None:
    if "." not in target_name:
        candidate = namespace.get(target_name)
        if callable(candidate):
            return None
        return BehaviorIssue(
            case="function lookup",
            expected=f"callable {target_name}",
            actual=type(candidate).__name__,
            details=f"Generated code did not define {target_name}.",
        )

    class_name, method_name = target_name.split(".", 1)
    class_candidate = namespace.get(class_name)
    if not isinstance(class_candidate, type):
        return BehaviorIssue(
            case="class lookup",
            expected=f"class {class_name}",
            actual=type(class_candidate).__name__,
            details=f"Generated code did not define class {class_name}.",
        )
    if not hasattr(class_candidate, method_name):
        return BehaviorIssue(
            case="method lookup",
            expected=f"method {target_name}",
            actual="missing",
            details=f"Generated class {class_name} did not define {method_name}.",
        )
    return None


def _resolve_case_callable(namespace: dict[str, Any], target_name: str, case: BehaviorCase) -> Any:
    if "." not in target_name:
        return namespace[target_name]
    class_name, method_name = target_name.split(".", 1)
    class_candidate = namespace[class_name]
    instance = class_candidate(*deepcopy(case.setup_args), **deepcopy(case.setup_kwargs))
    return getattr(instance, method_name)


def _validate_runtime_ast(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, BLOCKED_NODES):
            raise ValueError(f"Unsupported runtime node: {node.__class__.__name__}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_CALL_NAMES:
                raise ValueError(f"Blocked call: {node.func.id}")


def _strip_annotations(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.Lambda)):
            node.returns = None
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                arg.annotation = None
            if node.args.vararg:
                node.args.vararg.annotation = None
            if node.args.kwarg:
                node.args.kwarg.annotation = None
        elif isinstance(node, ast.AnnAssign):
            node.annotation = ast.Constant(value=None)
    ast.fix_missing_locations(tree)
    return tree
