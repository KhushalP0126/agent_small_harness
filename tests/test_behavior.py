import unittest
from dataclasses import asdict

from benchmarker import ROOT
from engines.branching_engine import BranchingEngine
from engines.hazards_engine import HazardsEngine
from engines.math_engine import MathEngine
from validation.behavior import mixed_hard_case_spec, validate_function_behavior
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


if __name__ == "__main__":
    unittest.main()
