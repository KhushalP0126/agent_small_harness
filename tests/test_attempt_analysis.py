from __future__ import annotations

import unittest

from agents.attempt_analysis import diagnostic_stagnant, draft_diff


class AttemptAnalysisTests(unittest.TestCase):
    def test_draft_diff_is_empty_only_for_identical_drafts(self) -> None:
        self.assertEqual(draft_diff("x = 1\n", "x = 1\n"), "")
        diff = draft_diff("x = 1\n", "x = 2\n")
        self.assertIn("-x = 1", diff)
        self.assertIn("+x = 2", diff)

    def test_stagnation_requires_a_repeated_unimproved_diagnostic(self) -> None:
        self.assertFalse(diagnostic_stagnant([]))
        self.assertFalse(diagnostic_stagnant([{"repeated": False, "improved": False}]))
        self.assertTrue(diagnostic_stagnant([{"repeated": True, "improved": False}]))
        self.assertFalse(diagnostic_stagnant([{"repeated": True, "improved": True}]))


if __name__ == "__main__":
    unittest.main()
