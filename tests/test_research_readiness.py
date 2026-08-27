from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness_kernel.research_readiness import (
    evaluate_research_readiness,
    render_readiness_markdown,
    render_readiness_svg,
)


class ResearchReadinessTests(unittest.TestCase):
    def test_missing_evidence_is_reported_without_fabrication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = evaluate_research_readiness(Path(tmp))

        self.assertLess(result["score"], 100)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["blockers"])

    def test_provider_health_is_mandatory_for_benchmark_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "docs/results/raw"
            raw.mkdir(parents=True)
            (raw / "fixture-qwen-1.5b-repeated-2026-08-16.json").write_text(
                json.dumps({
                    "schema_version": 3,
                    "run_count": 3,
                    "provenance": {},
                    "health": {"comparison_eligible": False},
                }),
                encoding="utf-8",
            )
            result = evaluate_research_readiness(root)

        qwen = next(item for item in result["categories"] if item["name"] == "qwen_local")
        self.assertFalse(qwen["passed"])
        self.assertIn("provider-healthy", qwen["blockers"][0])

    def test_visual_outputs_are_dependency_free_and_linked(self) -> None:
        result = {
            "score": 50,
            "status": "blocked",
            "categories": [
                {"name": "verification", "passed": True, "evidence": ["proof.json"]},
                {"name": "live", "passed": False, "evidence": []},
            ],
            "blockers": ["missing live receipt"],
        }
        svg = render_readiness_svg(result)
        report = render_readiness_markdown(result, svg_name="readiness.svg")

        self.assertIn("<svg", svg)
        self.assertIn("50%", svg)
        self.assertIn("readiness.svg", report)
        self.assertNotIn("```", report)


if __name__ == "__main__":
    unittest.main()
