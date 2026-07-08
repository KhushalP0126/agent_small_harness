from __future__ import annotations

import unittest

from validation.branch_loop_detector import build_branch_state_signature, detect_branching_loop


def attempt(
    source: str,
    worker: str,
    changed: bool,
    diff: str,
    violations: list[dict],
    behavior_issues: list[dict] | None = None,
) -> dict:
    payload = {
        "draft": source,
        "draft_source_worker": worker,
        "changed": changed,
        "diff": diff,
        "validation": {"is_compliant": not violations, "violations": violations},
        "behavior_validation": {"is_compliant": not behavior_issues, "issues": behavior_issues or []},
        "formal_validation": {"is_compliant": True, "issues": []},
        "diagnostic_deltas": [],
    }
    payload["branch_state_signature"] = build_branch_state_signature("build parser", payload).to_dict()
    return payload


def complexity(value: str = "9") -> dict:
    return {
        "kind": "cyclomatic_complexity",
        "engine": "engine-3-branching",
        "current_value": value,
        "allowed_value": "<= 7",
    }


class BranchLoopDetectorTests(unittest.TestCase):
    def test_simple_repeated_branch_loop_is_detected(self) -> None:
        attempts = [
            attempt("def f(): pass", "small_worker", True, "diff", [complexity()]),
            attempt("def f(): return None", "small_worker", True, "diff", [complexity()]),
        ]
        result = detect_branching_loop(attempts)
        self.assertTrue(result.detected)
        self.assertEqual(result.reason, "branching_loop_detected")

    def test_branch_cycle_a_b_a_is_detected(self) -> None:
        attempts = [
            attempt("a", "small_worker", True, "diff", [complexity()]),
            attempt("b", "architect_llm", True, "diff", [complexity()]),
            attempt("c", "small_worker", True, "diff", [complexity()]),
        ]
        result = detect_branching_loop(attempts)
        self.assertTrue(result.detected)
        self.assertEqual(result.reason, "branching_loop_detected")
        self.assertEqual(result.prior_attempt, 0)
        self.assertEqual(result.current_attempt, 2)

    def test_no_artifact_progress_is_detected(self) -> None:
        attempts = [
            attempt("a", "small_worker", True, "diff", [complexity()]),
            attempt("a", "small_worker", False, "", [complexity()]),
        ]
        result = detect_branching_loop(attempts)
        self.assertTrue(result.detected)
        self.assertEqual(result.reason, "no_new_artifact_progress")

    def test_retry_with_real_progress_is_not_flagged(self) -> None:
        attempts = [
            attempt("a", "small_worker", True, "diff", [complexity(), {"kind": "lint_error"}]),
            attempt("b", "small_worker", True, "diff", [complexity()]),
        ]
        result = detect_branching_loop(attempts)
        self.assertFalse(result.detected)


if __name__ == "__main__":
    unittest.main()
