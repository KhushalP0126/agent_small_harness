from __future__ import annotations

import unittest
from unittest.mock import patch

from validation.branch_loop_detector import (
    build_branch_state_signature,
    build_failure_signature,
    compare_drafts,
    detect_branching_loop,
    is_semantically_stagnant,
)


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
    def test_python_ast_ignores_whitespace_only_edits(self) -> None:
        comparison = compare_drafts("def f(x):\n return x + 1\n", "def f(x):\n    return x + 1\n")
        self.assertEqual(comparison.edit_ratio, 0.0)

    def test_substantive_ast_edit_is_not_stagnant(self) -> None:
        comparison = is_semantically_stagnant(
            "def f(x):\n return x + 1\n", "def f(x):\n return max(x, 0)\n",
            "validator:behavior:f", "validator:behavior:f",
        )
        self.assertFalse(comparison.semantically_stagnant)

    def test_failure_signature_uses_stable_symbol_identity(self) -> None:
        signature = build_failure_signature({
            "engine": "behavior-validator", "kind": "behavior_mismatch",
            "evidence": {"function_name": "f"},
        })
        self.assertEqual(signature, "behavior-validator:behavior_mismatch:f")

    def test_failure_signature_ignores_moving_line_numbers(self) -> None:
        first = build_failure_signature({
            "engine": "behavior-validator",
            "kind": "behavior_mismatch",
            "location": "parser.py:10:2",
            "evidence": {"case": {"name": "empty input"}},
        })
        second = build_failure_signature({
            "engine": "behavior-validator",
            "kind": "behavior_mismatch",
            "location": "parser.py:40:8",
            "evidence": {"case": {"name": "empty input"}},
        })
        self.assertEqual(first, second)

    def test_unavailable_tree_sitter_uses_conservative_token_fallback(self) -> None:
        with patch("engines.treesitter_support.parse_tree", side_effect=ValueError("grammar unavailable")):
            comparison = compare_drafts("int f(){return 1;}", "int f() { return 1; }", "c")
        self.assertEqual(comparison.representation, "tokens")
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
