from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.base import AgentResult
from scripts.run_raw_vs_harness import run_raw_vs_harness


class _WorkerSupplier:
    def generate_draft(self, _prompt: str) -> str:
        return "def identity(value):\n    return value\n"

    def repair_draft(self, draft: str, _prompt: str) -> str:
        return draft


class _ArchitectSupplier:
    def repair_draft(self, draft: str, _prompt: str) -> str:
        return draft


class _NaiveWorkerSupplier:
    def generate_draft(self, _prompt: str) -> str:
        return "def identity(value):\n    return -1\n"

    def repair_draft(self, _draft: str, prompt: str) -> str:
        if "Observed failures:" in prompt:
            return "def identity(value):\n    return value\n"
        return _draft


class _Controller:
    kwargs = {}

    def __init__(self, **kwargs):
        type(self).kwargs = kwargs

    def run(self, **_kwargs) -> AgentResult:
        return AgentResult(
            agent="generation-controller",
            payload={
                "final_status": "completed",
                "attempts": [{"draft_source_worker": "architect_llm"}],
            },
        )


class RawVsHarnessTests(unittest.TestCase):
    @staticmethod
    def _tasks() -> list[dict]:
        return [
            {
                "difficulty": 1,
                "name": "identity",
                "prompt": "Write identity(value).",
                "function_name": "identity",
                "cases": [{"name": "basic", "args": [3], "expected": 3}],
            }
        ]

    def test_architect_mode_wires_supplier_threshold_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.json"
            path.write_text(json.dumps(self._tasks()), encoding="utf-8")
            with (
                patch(
                    "scripts.run_raw_vs_harness.OllamaModelSupplier",
                    return_value=_WorkerSupplier(),
                ),
                patch(
                    "scripts.run_raw_vs_harness.ArchitectModelSupplier",
                    return_value=_ArchitectSupplier(),
                ),
                patch(
                    "scripts.run_raw_vs_harness.GenerationController",
                    _Controller,
                ),
            ):
                result = run_raw_vs_harness(
                    tasks_path=path,
                    model="test-model",
                    max_retries=2,
                    architect_after_repair_attempts=1,
                )

        self.assertEqual(result, 0)
        self.assertEqual(_Controller.kwargs["architect_after_repair_attempts"], 1)
        self.assertIsNotNone(_Controller.kwargs["architect_supplier"])
        self.assertEqual(_Controller.kwargs["max_retries"], 2)

    def test_repeated_mode_saves_paired_drafts_and_aggregate_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            path = Path(tmpdir) / "tasks.json"
            path.write_text(json.dumps(self._tasks()), encoding="utf-8")
            with (
                patch(
                    "scripts.run_raw_vs_harness.OllamaModelSupplier",
                    return_value=_WorkerSupplier(),
                ),
                patch(
                    "scripts.run_raw_vs_harness.ArchitectModelSupplier",
                    return_value=_ArchitectSupplier(),
                ),
                patch(
                    "scripts.run_raw_vs_harness.GenerationController",
                    _Controller,
                ),
            ):
                result = run_raw_vs_harness(
                    tasks_path=path,
                    model="test-model",
                    max_retries=2,
                    architect_after_repair_attempts=1,
                    samples=2,
                    save_artifacts=True,
                    artifact_root=root,
                )

            batch_dir = next(root.iterdir())
            summary = json.loads(
                (batch_dir / "raw_vs_harness_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result, 0)
            self.assertEqual(summary["samples"], 2)
            self.assertEqual(summary["total_pairs"], 2)
            self.assertEqual(summary["sample_harness_pass_range"], [1, 1])
            self.assertEqual(summary["statistics"]["raw"]["rate"], 1.0)
            self.assertEqual(
                summary["statistics"]["raw"]["sample_rate_variance"],
                0.0,
            )
            self.assertEqual(
                len(summary["statistics"]["harness"]["wilson_95_ci"]),
                2,
            )
            self.assertTrue(
                (batch_dir / "sample_1" / "01_identity" / "raw_draft.py").is_file()
            )
            self.assertTrue(
                (
                    batch_dir
                    / "sample_2"
                    / "01_identity"
                    / "attempt_timeline.json"
                ).is_file()
            )

    def test_naive_baseline_saves_one_repair_ablation_and_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            path = Path(tmpdir) / "tasks.json"
            path.write_text(json.dumps(self._tasks()), encoding="utf-8")
            with (
                patch(
                    "scripts.run_raw_vs_harness.OllamaModelSupplier",
                    return_value=_NaiveWorkerSupplier(),
                ),
                patch(
                    "scripts.run_raw_vs_harness.GenerationController",
                    _Controller,
                ),
            ):
                result = run_raw_vs_harness(
                    tasks_path=path,
                    model="test-model",
                    max_retries=1,
                    samples=2,
                    include_naive_baseline=True,
                    save_artifacts=True,
                    artifact_root=root,
                )

            batch_dir = next(root.iterdir())
            summary = json.loads(
                (batch_dir / "raw_vs_harness_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result, 0)
            self.assertTrue(summary["naive_baseline_enabled"])
            self.assertEqual(summary["raw_passes"], 0)
            self.assertEqual(summary["naive_passes"], 2)
            self.assertEqual(summary["naive_repair_calls"], 2)
            self.assertEqual(summary["naive_recovered"], 2)
            self.assertEqual(summary["naive_pass_rate"], 1.0)
            self.assertEqual(summary["statistics"]["naive"]["sample_rates"], [1.0, 1.0])
            self.assertTrue(
                (batch_dir / "sample_1" / "01_identity" / "naive_draft.py").is_file()
            )
            self.assertTrue(
                (
                    batch_dir
                    / "sample_1"
                    / "01_identity"
                    / "naive_behavior.json"
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
