import unittest
from dataclasses import asdict

from benchmarker import ROOT
from engines.branching_engine import BranchingEngine
from engines.hazards_engine import HazardsEngine
from engines.math_engine import MathEngine
from validation.behavior import BehaviorCase, FunctionBehaviorSpec, mixed_hard_case_spec, validate_function_behavior
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


if __name__ == "__main__":
    unittest.main()
