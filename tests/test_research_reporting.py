from __future__ import annotations

import unittest

from harness_kernel.e2e_benchmark import AgentRunMetrics, BenchmarkTask
from harness_kernel.research_reporting import benchmark_health, run_repeated_paired_benchmark, summarize_reports
from scripts.render_research_report import _scope_lines, _variant_lines


class ResearchReportingTests(unittest.TestCase):
    def test_repeated_report_keeps_raw_runs_and_describes_variance(self) -> None:
        tasks = [BenchmarkTask("one", "inspection", "inspect one file")]
        baseline_calls = 0
        shielded_calls = 0

        def baseline(_task: BenchmarkTask) -> AgentRunMetrics:
            nonlocal baseline_calls
            baseline_calls += 1
            return AgentRunMetrics(True, 10 * baseline_calls, 2, 0, 0, 0.1)

        def shielded(_task: BenchmarkTask) -> AgentRunMetrics:
            nonlocal shielded_calls
            shielded_calls += 1
            return AgentRunMetrics(True, 8, 4 * shielded_calls, 1, 0, 0.2)

        report = run_repeated_paired_benchmark(tasks, baseline, shielded, runs=3)

        self.assertEqual(report["run_count"], 3)
        self.assertEqual(len(report["runs"]), 3)
        self.assertEqual(report["summary"]["baseline_tokens"]["count"], 3)
        self.assertGreater(report["summary"]["baseline_tokens"]["stdev"], 0)
        self.assertEqual(report["summary"]["shielded_tool_calls"]["mean"], 1)
        self.assertTrue(report["health"]["comparison_eligible"])
        self.assertIsNotNone(report["comparison_summary"])

    def test_empty_architect_response_rejects_comparison_aggregation(self) -> None:
        task = BenchmarkTask("one", "inspection", "inspect one file")
        failed = AgentRunMetrics(
            False,
            0,
            0,
            0,
            0,
            0.1,
            "RuntimeError: Architect API returned an empty response after 3 attempts.",
        )
        succeeded = AgentRunMetrics(True, 10, 2, 0, 0, 0.1)
        report = run_repeated_paired_benchmark(
            [task], lambda _task: failed, lambda _task: succeeded, runs=2
        )
        self.assertFalse(report["health"]["comparison_eligible"])
        self.assertEqual(report["health"]["provider_failure_count"], 2)
        self.assertIsNone(report["comparison_summary"])

    def test_task_failure_does_not_look_like_provider_failure(self) -> None:
        report = {
            "results": [
                {
                    "task": {"task_id": "invalid-candidate"},
                    "baseline": {"success": False, "error": "validation failed"},
                    "shielded": {"success": True},
                }
            ]
        }
        self.assertTrue(benchmark_health([report])["comparison_eligible"])

    def test_repeated_report_requires_multiple_runs(self) -> None:
        task = BenchmarkTask("one", "inspection", "inspect one file")
        metric = AgentRunMetrics(True, 1, 1, 0, 0, 0.1)
        with self.assertRaisesRegex(ValueError, "at least 2"):
            run_repeated_paired_benchmark([task], lambda _task: metric, lambda _task: metric, runs=1)

    def test_summary_derives_duration_and_tool_call_totals(self) -> None:
        report = {
            "baseline_successes": 1,
            "shielded_successes": 1,
            "baseline_tokens": 3,
            "shielded_tokens": 5,
            "token_delta": -2,
            "token_reduction_ratio": -2 / 3,
            "results": [
                {
                    "baseline": {"tool_calls": 2, "duration_seconds": 1.5},
                    "shielded": {"tool_calls": 3, "duration_seconds": 2.5},
                }
            ],
        }
        summary = summarize_reports([report])
        self.assertEqual(summary["baseline_tool_calls"]["mean"], 2)
        self.assertEqual(summary["shielded_duration_seconds"]["mean"], 2.5)

    def test_renderer_deduplicates_variant_identity_and_surfaces_scope(self) -> None:
        variants = {
            "baseline": [
                {"provider": "ollama", "model": "qwen", "context_window": 8192, "scope": "python-only"},
                {"provider": "ollama", "model": "qwen", "context_window": 8192, "scope": "python-only"},
            ],
            "shielded": [],
        }
        self.assertEqual(len([line for line in _variant_lines(variants) if "ollama" in line]), 1)
        self.assertEqual(_scope_lines(variants), ["- Scope: `python-only`"])


if __name__ == "__main__":
    unittest.main()
