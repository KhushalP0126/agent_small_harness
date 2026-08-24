from __future__ import annotations

import unittest

from agents.repair_strategy import (
    DETERMINISTIC_TRANSFORM,
    JSON_PATCH,
    RepairStrategyAgent,
    apply_deterministic_transform,
    apply_symbol_replacement_patch,
    parse_symbol_replacement_patch,
)


class RepairStrategyTighteningTests(unittest.TestCase):
    def test_known_forbidden_import_routes_deterministically(self) -> None:
        decision = RepairStrategyAgent().decide_repeated_failure([{"kind": "external_dependency"}])
        self.assertEqual(decision.mode, DETERMINISTIC_TRANSFORM)

    def test_unknown_repeated_failure_routes_to_json_patch(self) -> None:
        decision = RepairStrategyAgent().decide_repeated_failure([{"kind": "behavior_mismatch"}])
        self.assertEqual(decision.mode, JSON_PATCH)

    def test_forbidden_import_transform_requires_exact_evidence(self) -> None:
        result = apply_deterministic_transform(
            "import requests\n\ndef f():\n    return 1\n",
            {"kind": "external_dependency", "evidence": {"module": "requests"}},
        )
        self.assertNotIn("requests", result)
        self.assertIn("def f", result)

    def test_valid_single_symbol_patch(self) -> None:
        patch = parse_symbol_replacement_patch({
            "target_symbol": "f", "action": "replace_symbol",
            "replacement_source": "def f(x):\n    return x + 1",
        })
        self.assertIn("return x + 1", apply_symbol_replacement_patch("def f(x):\n    return x\n", patch))

    def test_invalid_patch_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_symbol_replacement_patch('{"action":"replace_symbol"}')


if __name__ == "__main__":
    unittest.main()
