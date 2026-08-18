import unittest

from harness_kernel.e2e_benchmark import (
    AgentRunMetrics,
    BenchmarkTask,
    run_paired_benchmark,
    run_three_arm_benchmark,
)


class EndToEndBenchmarkTests(unittest.TestCase):
    def test_paired_report_measures_token_reduction_and_outcomes(self) -> None:
        tasks = [
            BenchmarkTask("one", "inspect", "inspect one"),
            BenchmarkTask("two", "repair", "repair two"),
        ]

        def baseline(_task):
            return AgentRunMetrics(True, 80, 20, 1, 0, 1.0)

        def shielded(_task):
            return AgentRunMetrics(True, 30, 10, 3, 1, 1.5)

        report = run_paired_benchmark(tasks, baseline, shielded)

        self.assertEqual(report["task_count"], 2)
        self.assertEqual(report["baseline_tokens"], 200)
        self.assertEqual(report["shielded_tokens"], 80)
        self.assertEqual(report["token_delta"], 120)
        self.assertAlmostEqual(report["token_reduction_ratio"], 0.6)
        self.assertEqual(report["baseline_successes"], 2)
        self.assertEqual(report["shielded_successes"], 2)

    def test_failed_runs_remain_visible_in_report(self) -> None:
        task = BenchmarkTask("one", "repair", "repair")
        failed = AgentRunMetrics(False, 5, 0, 0, 0, 0.2, "backend unavailable")
        successful = AgentRunMetrics(True, 3, 2, 2, 0, 0.3)

        report = run_paired_benchmark([task], lambda _task: failed, lambda _task: successful)

        self.assertEqual(report["baseline_successes"], 0)
        self.assertEqual(
            report["results"][0]["baseline"]["error"],
            "backend unavailable",
        )

    def test_three_arm_report_keeps_regressions_and_routed_coverage_visible(self) -> None:
        tasks = [BenchmarkTask("one", "repair", "one"), BenchmarkTask("two", "repair", "two")]

        def baseline(_task):
            return AgentRunMetrics(True, 2, 2, 0, 0, 0.1)

        def generic(task):
            return AgentRunMetrics(task.task_id == "one", 3, 3, 0, 0, 0.1)

        def routed(task):
            return AgentRunMetrics(
                True,
                4,
                4,
                0,
                0,
                0.1,
                metadata={"repair_route": {"classified": task.task_id == "one"}},
            )

        report = run_three_arm_benchmark(tasks, baseline, generic, routed)
        self.assertEqual(report["arms"]["routed"]["successes"], 2)
        self.assertEqual(report["regressions"]["generic"]["regressed_tasks"], ["two"])
        self.assertEqual(report["regressions"]["routed"]["rate"], 0.0)
        self.assertEqual(report["routed_coverage"]["classified_tasks"], 1)
