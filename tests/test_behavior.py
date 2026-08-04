import unittest
from dataclasses import asdict

from benchmarker import ROOT
from engines.branching_engine import BranchingEngine
from engines.hazards_engine import HazardsEngine
from engines.math_engine import MathEngine
from validation.behavior import (
    BehaviorCase,
    FunctionBehaviorSpec,
    behavior_result_from_trace,
    execute_behavior_trace,
    mixed_hard_case_spec,
    validate_function_behavior,
)
from validation.debugger import (
    build_debugger_hints,
    localize_contract_failure,
    minimal_failing_reproducer,
)
from validation.policy import validate_findings


class BehaviorValidationTests(unittest.TestCase):
    def test_mixed_hard_fixture_matches_behavior_spec(self) -> None:
        source = (ROOT / "data" / "snippets" / "mixed_hard_case.py").read_text(encoding="utf-8")
        result = validate_function_behavior(source, mixed_hard_case_spec())
        self.assertTrue(result.is_compliant, [asdict(issue) for issue in result.issues])

    def test_static_clean_hallucination_fails_behavior_spec(self) -> None:
        hallucinated_source = """
def analyze(matrix):
    return 0
"""
        findings = [
            finding
            for engine in (MathEngine(), HazardsEngine(), BranchingEngine())
            for finding in engine.scan(hallucinated_source)
        ]
        static_result = validate_findings(findings)
        self.assertTrue(static_result.is_compliant)

        behavior_result = validate_function_behavior(hallucinated_source, mixed_hard_case_spec())
        self.assertFalse(behavior_result.is_compliant)
        self.assertTrue(any(issue.case == "covers all value classes" for issue in behavior_result.issues))

    def test_behavior_validator_rejects_unsafe_runtime_constructs(self) -> None:
        unsafe_source = """
import os

def analyze(matrix):
    return 0
"""
        result = validate_function_behavior(unsafe_source, mixed_hard_case_spec())
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.issues[0].case, "load")

    def test_behavior_validator_times_out_infinite_loop(self) -> None:
        source = """
def analyze(matrix):
    while True:
        pass
"""
        result = validate_function_behavior(source, mixed_hard_case_spec(), timeout_seconds=0.05)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.issues[0].case, "timeout")

    def test_behavior_validator_allows_value_error_handling(self) -> None:
        source = """
def parse_value(value):
    try:
        return int(value)
    except ValueError:
        return value
"""
        spec = FunctionBehaviorSpec(
            function_name="parse_value",
            cases=[
                BehaviorCase(name="integer", args=("7",), expected=7),
                BehaviorCase(name="string", args=("hello",), expected="hello"),
            ],
        )
        result = validate_function_behavior(source, spec)
        self.assertTrue(result.is_compliant, [asdict(issue) for issue in result.issues])

    def test_behavior_validator_allows_safe_map_builtin(self) -> None:
        source = """
def parse_values(values):
    return list(map(int, values))
"""
        spec = FunctionBehaviorSpec(
            function_name="parse_values",
            cases=[
                BehaviorCase(name="values", args=(["1", "2", "3"],), expected=[1, 2, 3]),
            ],
        )
        result = validate_function_behavior(source, spec)
        self.assertTrue(result.is_compliant, [asdict(issue) for issue in result.issues])

    def test_behavior_validator_allows_safe_str_builtin(self) -> None:
        source = """
def stringify(value):
    return str(value)
"""
        spec = FunctionBehaviorSpec(
            function_name="stringify",
            cases=[
                BehaviorCase(name="integer", args=(7,), expected="7"),
            ],
        )
        result = validate_function_behavior(source, spec)
        self.assertTrue(result.is_compliant, [asdict(issue) for issue in result.issues])

    def test_behavior_validator_can_check_class_methods(self) -> None:
        source = """
class Counter:
    def __init__(self, value):
        self.value = value

    def add(self, amount):
        return self.value + amount
"""
        spec = FunctionBehaviorSpec(
            function_name="Counter.add",
            cases=[
                BehaviorCase(name="adds from instance state", args=(4,), setup_args=(3,), expected=7),
            ],
        )
        result = validate_function_behavior(source, spec)
        self.assertTrue(result.is_compliant, [asdict(issue) for issue in result.issues])

    def test_behavior_validator_catches_class_method_runtime_errors(self) -> None:
        source = """
class PongState:
    def __init__(self, left_score, right_score):
        self.left_score = left_score
        self.right_score = right_score

    def score_left(self):
        return self.score1 + 1
"""
        spec = FunctionBehaviorSpec(
            function_name="PongState.score_left",
            cases=[
                BehaviorCase(name="left score increments", args=(), setup_args=(2, 0), expected=3),
            ],
        )
        result = validate_function_behavior(source, spec)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.issues[0].actual, "AttributeError")


class ExecutionTraceTests(unittest.TestCase):
    def _spec(self) -> FunctionBehaviorSpec:
        return FunctionBehaviorSpec(
            function_name="add",
            cases=[BehaviorCase(name="basic", args=(2, 3), expected=5)],
        )

    def test_trace_records_expected_match(self) -> None:
        trace = execute_behavior_trace("def add(a, b):\n    return a + b\n", self._spec())
        self.assertTrue(trace.loaded)
        self.assertTrue(trace.cases[0].matched)
        self.assertEqual(trace.cases[0].returned, "5")

    def test_trace_records_exception_details(self) -> None:
        trace = execute_behavior_trace("def add(a, b):\n    return a // 0\n", self._spec())
        self.assertEqual(trace.cases[0].exception_type, "ZeroDivisionError")
        self.assertTrue(trace.cases[0].traceback)

    def test_trace_captures_stdout_and_behavior_issue_cites_it(self) -> None:
        source = "def add(a, b):\n    print('observed', a, b)\n    return a - b\n"
        trace = execute_behavior_trace(source, self._spec())
        self.assertIn("observed 2 3", trace.cases[0].stdout)
        result = behavior_result_from_trace(trace)
        self.assertFalse(result.is_compliant)
        self.assertIn("stdout: observed 2 3", result.issues[0].details)

    def test_trace_captures_state_delta_for_mutated_arguments(self) -> None:
        source = """
def append_item(values):
    values.append(3)
    return values
"""
        spec = FunctionBehaviorSpec(
            function_name="append_item",
            cases=[BehaviorCase(name="append", args=([1, 2],), expected=[1, 2, 3])],
        )
        trace = execute_behavior_trace(source, spec)
        self.assertIn("before=", trace.cases[0].state_delta)
        self.assertIn("after=", trace.cases[0].state_delta)
        self.assertTrue(trace.cases[0].steps)
        self.assertTrue(trace.cases[0].step_deltas)

    def test_result_derived_from_trace_matches_validator(self) -> None:
        source = "def add(a, b):\n    return a - b\n"
        spec = self._spec()
        derived = behavior_result_from_trace(execute_behavior_trace(source, spec))
        direct = validate_function_behavior(source, spec)
        self.assertEqual(derived.is_compliant, direct.is_compliant)
        self.assertEqual(
            [asdict(issue) for issue in derived.issues],
            [asdict(issue) for issue in direct.issues],
        )

    def test_debugger_hints_include_state_delta_and_localize_dependencies(self) -> None:
        trace = execute_behavior_trace(
            "def add(values):\n    values.append(4)\n    return values\n",
            FunctionBehaviorSpec(
                function_name="add",
                cases=[BehaviorCase(name="delta", args=([1],), expected=[1])],
            ),
        )
        hints = build_debugger_hints(trace)
        self.assertTrue(any("state delta" in hint for hint in hints))
        self.assertEqual(
            localize_contract_failure(
                "b",
                {"b": ["a"]},
                {"a": False, "b": False},
            ),
            ["a"],
        )
        reproducer = minimal_failing_reproducer(trace)
        self.assertEqual(reproducer["schema_version"], 1)
        self.assertEqual(reproducer["cases"][0]["name"], "delta")


if __name__ == "__main__":
    unittest.main()
