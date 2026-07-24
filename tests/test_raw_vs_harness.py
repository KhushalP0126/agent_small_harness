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


if __name__ == "__main__":
    unittest.main()
