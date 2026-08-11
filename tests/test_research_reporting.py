from __future__ import annotations

import unittest

from harness_kernel.e2e_benchmark import AgentRunMetrics, BenchmarkTask
from harness_kernel.research_reporting import run_repeated_paired_benchmark, summarize_reports


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


if __name__ == "__main__":
    unittest.main()
