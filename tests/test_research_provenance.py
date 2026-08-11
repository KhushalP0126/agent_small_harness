from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path

from agents.artifact_manager import ARTIFACT_SCHEMA_VERSION, ArtifactManager
from harness_kernel.e2e_benchmark import AgentRunMetrics, BenchmarkTask, run_paired_benchmark
from harness_kernel.live_session import build_live_session_receipt
from harness_kernel.provenance import collect_provenance, configured_model_settings
from scripts.render_local_model_comparison import render_comparison
from scripts.render_research_report import render_report


class ResearchProvenanceTests(unittest.TestCase):
    def test_provenance_records_environment_commit_and_corpus_digest(self) -> None:
        root = Path(__file__).resolve().parents[1]
        provenance = collect_provenance(
            repository_root=root,
            task_corpus=root / "data" / "research_fixture_tasks.json",
            settings={"model": "qwen2.5-coder:1.5b"},
        )
        self.assertIn("captured_at_utc", provenance)
        self.assertIn("commit", provenance["repository"])
        self.assertIn("os", provenance["environment"])
        self.assertEqual(provenance["task_corpus"]["path"], "data/research_fixture_tasks.json")
        self.assertEqual(len(provenance["task_corpus"]["sha256"]), 64)

    def test_configured_model_settings_excludes_secrets(self) -> None:
        settings = configured_model_settings()
        self.assertEqual(settings["local_model"], "qwen2.5-coder:1.5b")
        self.assertNotIn("api_key", " ".join(settings).casefold())

    def test_artifacts_receive_provenance_without_caller_plumbing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = ArtifactManager(directory)
            paths = manager.create_run(prefix="provenance")
            manager.save_session({"attempts": []}, paths)
            metadata = json.loads((paths.run_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["schema_version"], ARTIFACT_SCHEMA_VERSION)
        self.assertIn("provenance", metadata)
        self.assertIn("architect_model", metadata["provenance"]["settings"])

    def test_paired_report_preserves_runner_metadata(self) -> None:
        task = BenchmarkTask("one", "inspection", "inspect")
        metric = AgentRunMetrics(True, 2, 3, 1, 0, 0.1, metadata={"model": "fixture"})
        report = run_paired_benchmark([task], lambda _task: metric, lambda _task: metric)
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["results"][0]["baseline"]["metadata"]["model"], "fixture")

    def test_report_renderer_keeps_failure_and_declines_token_savings_claim(self) -> None:
        root = Path(__file__).resolve().parents[1]
        provenance = collect_provenance(
            repository_root=root,
            task_corpus=root / "data" / "research_fixture_tasks.json",
        )
        payload = {
            "schema_version": 2,
            "provenance": provenance,
            "run_count": 2,
            "variant_metadata": {"baseline": [{"provider": "fixture", "model": "one"}], "shielded": [{"provider": "fixture", "model": "one"}]},
            "summary": {
                "baseline_successes": {"mean": 1},
                "shielded_successes": {"mean": 0},
                "baseline_tokens": {"mean": 10},
                "shielded_tokens": {"mean": 20},
                "baseline_tool_calls": {"mean": 0},
                "shielded_tool_calls": {"mean": 2},
                "baseline_duration_seconds": {"mean": 1.0},
                "shielded_duration_seconds": {"mean": 2.0},
                "token_delta": {"mean": -10},
            },
            "runs": [{"results": [{"task": {"task_id": "failed"}, "baseline": {"success": True}, "shielded": {"success": False, "error": "turn_limit"}}]}],
        }
        rendered = render_report(payload, title="Fixture report")
        self.assertIn("turn_limit", rendered)
        self.assertIn("does not claim token savings", rendered)

    def test_live_session_receipt_requires_multi_file_accept_and_reject(self) -> None:
        root = Path(__file__).resolve().parents[1]
        receipt = build_live_session_receipt(
            repository_root=root,
            scenario="multi_file_edit",
            prompt_summary="Add a reviewed cross-module helper.",
            provider="fixture",
            model="fixture-model",
            approvals=["first=approved", "second=rejected"],
            validation_status="passed",
            outcome="manual review complete",
            tool_calls=2,
            proposed_diff="diff --git a/a.py b/a.py",
        )
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(receipt["approvals"][1]["decision"], "rejected")
        self.assertEqual(len(receipt["proposed_diff_sha256"]), 64)

    def test_live_session_receipt_rejects_secret_like_text(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(ValueError, "secrets"):
            build_live_session_receipt(
                repository_root=root,
                scenario="plain_question",
                prompt_summary="DEEPSEEK_API_KEY=secret",
                provider="fixture",
                model="fixture-model",
                approvals=[],
                validation_status="not_applicable",
                outcome="answered",
                tool_calls=0,
            )

    def test_model_comparison_reports_observed_difference_without_scaling_claim(self) -> None:
        def payload(successes: int, tokens: int) -> dict:
            rows = [
                {
                    "baseline": {"duration_seconds": 1, "tool_calls": 0},
                    "shielded": {"duration_seconds": 2, "tool_calls": 1},
                }
                for _ in range(10)
            ]
            return {
                "schema_version": 2,
                "report": {
                    "baseline_successes": 10,
                    "shielded_successes": successes,
                    "baseline_tokens": 100,
                    "shielded_tokens": tokens,
                    "results": rows,
                },
            }

        rendered = render_comparison(payload(8, 200), payload(9, 300))
        self.assertIn("more shielded completions", rendered)
        self.assertIn("not a parameter-scaling law", rendered)


if __name__ == "__main__":
    unittest.main()
