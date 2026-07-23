from __future__ import annotations

import ast
import contextlib
import io
import multiprocessing as mp
import queue
import time
import traceback
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_BEHAVIOR_TIMEOUT_SECONDS = 1.0
_MAX_CAPTURE_CHARS = 4000
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
    "print": print,
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


@dataclass
class CaseTrace:
    """Observed runtime state for a single executed behavior case."""

    name: str
    args: str = ""
    kwargs: str = ""
    returned: str = ""
    expected: str = ""
    matched: bool = False
    stdout: str = ""
    stderr: str = ""
    exception_type: str = ""
    exception_message: str = ""
    traceback: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class ExecutionTrace:
    """A real execution record for a draft against its behavior spec.

    This is evidence produced by actually running the code, not a structural
    inference. ``BehaviorResult`` is derived from it, and it is also the raw
    material a debugger can diff against the spec sheet.
    """

    function_name: str
    fatal_case: str = ""
    fatal_expected: str = ""
    fatal_actual: str = ""
    fatal_details: str = ""
    cases: list[CaseTrace] = field(default_factory=list)

    @property
    def loaded(self) -> bool:
        return self.fatal_case == ""


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


def serialize_execution_trace(trace: ExecutionTrace) -> dict[str, Any]:
    return asdict(trace)


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


def execute_behavior_trace(
    source: str,
    spec: FunctionBehaviorSpec,
    timeout_seconds: float = DEFAULT_BEHAVIOR_TIMEOUT_SECONDS,
) -> ExecutionTrace:
    """Run ``source`` against ``spec`` in an isolated process and return a trace.

    The draft is executed for real; the returned :class:`ExecutionTrace` records
    per-case return values, captured stdout/stderr, and exceptions. Timeouts and
    lost results are reported as fatal cases so callers always get a trace.
    """

    ctx = _multiprocessing_context()
    result_queue = ctx.Queue()
    process = ctx.Process(target=_trace_worker, args=(source, spec, result_queue))
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join()
        return ExecutionTrace(
            function_name=spec.function_name,
            fatal_case="timeout",
            fatal_expected=f"complete within {timeout_seconds:g}s",
            fatal_actual="timeout",
            fatal_details="Behavior validation exceeded the sandbox timeout.",
        )
    try:
        payload = result_queue.get_nowait()
    except queue.Empty:
        return ExecutionTrace(
            function_name=spec.function_name,
            fatal_case="load",
            fatal_expected="behavior result",
            fatal_actual=f"process exit {process.exitcode}",
            fatal_details="Behavior sandbox exited without returning a result.",
        )
    return _deserialize_execution_trace(payload)


def behavior_result_from_trace(trace: ExecutionTrace) -> BehaviorResult:
    """Derive the pass/fail behavior result from an execution trace."""

    if trace.fatal_case:
        return BehaviorResult(
            is_compliant=False,
            issues=[
                BehaviorIssue(
                    case=trace.fatal_case,
                    expected=trace.fatal_expected,
                    actual=trace.fatal_actual,
                    details=trace.fatal_details,
                )
            ],
        )
    issues: list[BehaviorIssue] = []
    for case in trace.cases:
        if case.exception_type:
            details = _case_runtime_details(case, case.exception_message)
            issues.append(
                BehaviorIssue(
                    case=case.name,
                    expected=case.expected,
                    actual=case.exception_type,
                    details=details,
                )
            )
        elif not case.matched:
            details = _case_runtime_details(
                case, "Return value did not match the behavior spec."
            )
            issues.append(
                BehaviorIssue(
                    case=case.name,
                    expected=case.expected,
                    actual=case.returned,
                    details=details,
                )
            )
    return BehaviorResult(is_compliant=not issues, issues=issues)


def _case_runtime_details(case: CaseTrace, summary: str) -> str:
    parts = [summary]
    if case.stdout:
        parts.append(f"stdout: {case.stdout}")
    if case.stderr:
        parts.append(f"stderr: {case.stderr}")
    if case.traceback:
        parts.append(f"traceback: {case.traceback}")
    return _clip("\n".join(parts))


def validate_function_behavior(
    source: str,
    spec: FunctionBehaviorSpec,
    timeout_seconds: float = DEFAULT_BEHAVIOR_TIMEOUT_SECONDS,
) -> BehaviorResult:
    """Execute the draft and return whether it satisfies the behavior spec.

    Behavior is backed by a real run: the sandbox emits an execution trace and
    the pass/fail result is derived from it, preserving the previous semantics
    and issue messages.
    """

    return behavior_result_from_trace(execute_behavior_trace(source, spec, timeout_seconds))


def _multiprocessing_context() -> mp.context.BaseContext:
    if "fork" in mp.get_all_start_methods():
        return mp.get_context("fork")
    return mp.get_context()


def _trace_worker(source: str, spec: FunctionBehaviorSpec, result_queue: Any) -> None:
    result_queue.put(serialize_execution_trace(_execute_behavior_trace_inline(source, spec)))


def _deserialize_execution_trace(payload: dict[str, Any]) -> ExecutionTrace:
    cases = [CaseTrace(**case) for case in payload.get("cases", [])]
    data = {key: value for key, value in payload.items() if key != "cases"}
    return ExecutionTrace(cases=cases, **data)


def _clip(text: str) -> str:
    return text[-_MAX_CAPTURE_CHARS:] if len(text) > _MAX_CAPTURE_CHARS else text


def _execute_behavior_trace_inline(source: str, spec: FunctionBehaviorSpec) -> ExecutionTrace:
    try:
        tree = ast.parse(source)
        _validate_runtime_ast(tree)
        tree = _strip_annotations(tree)
        namespace: dict[str, Any] = {"__builtins__": SAFE_BUILTINS, "__name__": "__behavior_check__"}
        exec(compile(tree, "<behavior-check>", "exec"), namespace)
    except Exception as exc:  # noqa: BLE001 - reported as trace evidence
        return ExecutionTrace(
            function_name=spec.function_name,
            fatal_case="load",
            fatal_expected="valid executable Python",
            fatal_actual=exc.__class__.__name__,
            fatal_details=str(exc),
        )

    lookup_issue = _candidate_lookup_issue(namespace, spec.function_name)
    if lookup_issue is not None:
        return ExecutionTrace(
            function_name=spec.function_name,
            fatal_case=lookup_issue.case,
            fatal_expected=lookup_issue.expected,
            fatal_actual=lookup_issue.actual,
            fatal_details=lookup_issue.details,
        )

    trace = ExecutionTrace(function_name=spec.function_name)
    for case in spec.cases:
        trace.cases.append(_run_case(namespace, spec.function_name, case))
    return trace


def _run_case(namespace: dict[str, Any], function_name: str, case: BehaviorCase) -> CaseTrace:
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    record = CaseTrace(
        name=case.name,
        args=repr(case.args),
        kwargs=repr(case.kwargs),
        expected=repr(case.expected),
    )
    start = time.perf_counter()
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            candidate = _resolve_case_callable(namespace, function_name, case)
            actual = candidate(*deepcopy(case.args), **deepcopy(case.kwargs))
    except Exception as exc:  # noqa: BLE001 - reported as trace evidence
        record.elapsed_seconds = time.perf_counter() - start
        record.exception_type = exc.__class__.__name__
        record.exception_message = str(exc)
        record.traceback = _clip(traceback.format_exc())
        record.stdout = _clip(stdout_buffer.getvalue())
        record.stderr = _clip(stderr_buffer.getvalue())
        return record
    record.elapsed_seconds = time.perf_counter() - start
    record.returned = repr(actual)
    record.matched = actual == case.expected
    record.stdout = _clip(stdout_buffer.getvalue())
    record.stderr = _clip(stderr_buffer.getvalue())
    return record


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
