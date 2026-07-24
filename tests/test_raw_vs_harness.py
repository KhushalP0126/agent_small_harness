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
    def test_architect_mode_wires_supplier_threshold_and_metrics(self) -> None:
        tasks = [
            {
                "difficulty": 1,
                "name": "identity",
                "prompt": "Write identity(value).",
                "function_name": "identity",
                "cases": [{"name": "basic", "args": [3], "expected": 3}],
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.json"
            path.write_text(json.dumps(tasks), encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
