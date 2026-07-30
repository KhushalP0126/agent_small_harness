import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.artifact_manager import ArtifactManager
from harness_kernel.event_stream import EVENT_FD_ENV, event_sink_from_env
from harness_kernel.tui_bridge import Bridge, EventWriter, _validated_args


class TuiBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output = io.StringIO()
        self.bridge = Bridge(EventWriter(self.output))

    def events(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.output.getvalue().splitlines()
            if line.strip()
        ]

    def test_unknown_command_is_structured_log(self) -> None:
        self.bridge.handle({"cmd": "missing"})
        self.assertEqual(self.events()[0]["type"], "log")
        self.assertEqual(self.events()[0]["level"], "error")

    def test_profile_samples_emits_typed_result(self) -> None:
        self.bridge.handle(
            {
                "cmd": "profile_samples",
                "loop_order": "MKN",
                "samples_ns": [12, 10, 11],
                "cache_misses": 4,
            }
        )
        self.assertEqual(
            self.events(),
            [
                {
                    "type": "profiling_result",
                    "loop_order": "MKN",
                    "runtime_ns": 11,
                    "spread_ns": 2,
                    "cache_misses": 4,
                }
            ],
        )

    def test_compute_shield_emits_aggregate_event(self) -> None:
        self.bridge.handle(
            {
                "cmd": "compute_shield",
                "phase": 3,
                "tasks": [
                    {
                        "task": "matrix",
                        "baseline_tokens": 100,
                        "shielded_tokens": 30,
                    }
                ],
            }
        )
        event = self.events()[0]
        self.assertEqual(event["type"], "compute_shield_metrics")
        self.assertEqual(event["delta"], 70)

    def test_repo_map_emits_mermaid_from_real_mapper(self) -> None:
        self.bridge.handle({"cmd": "repo_map", "root": ".", "focus": "agents"})
        event = self.events()[0]
        self.assertEqual(event["type"], "repo_map")
        self.assertTrue(event["mermaid"].startswith("flowchart"))

    def test_argument_allowlist_rejects_unknown_flag(self) -> None:
        with self.assertRaises(ValueError):
            _validated_args(["--not-real", "value"])

    @unittest.skipUnless(os.name == "posix", "inherited fd test requires POSIX")
    def test_inherited_event_sink_is_independent_from_stdout(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            with patch.dict(os.environ, {EVENT_FD_ENV: str(write_fd)}):
                sink = event_sink_from_env()
                self.assertIsNotNone(sink)
                sink({"type": "compile_gate_result", "status": "pass", "errors": []})
            os.close(write_fd)
            write_fd = -1
            with os.fdopen(read_fd, "r", encoding="utf-8") as stream:
                event = json.loads(stream.readline())
            read_fd = -1
            self.assertEqual(event["type"], "compile_gate_result")
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            if read_fd >= 0:
                os.close(read_fd)

    def test_history_list_mode_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArtifactManager(Path(tmpdir))
            created = []
            for index in range(3):
                run_id = f"run-{index}"
                paths = manager.create_run(run_id=run_id)
                manager.checkpoint(
                    {
                        "target": f"task-{index}.py",
                        "final_status": "manual_review_required",
                        "attempts": [{"attempt": 0}],
                    },
                    paths,
                )
                created.append(run_id)
            bridge = Bridge(EventWriter(self.output), artifact_root=Path(tmpdir))
            bridge.handle({"cmd": "history", "limit": 2})
        event = self.events()[0]
        self.assertEqual(event["type"], "history_list")
        self.assertEqual(len(event["runs"]), 2)
        for summary in event["runs"]:
            self.assertIn(summary["run_id"], created)
            self.assertEqual(summary["final_status"], "manual_review_required")
            self.assertEqual(summary["attempt_count"], 1)

    def test_history_detail_missing_run_warns_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = Bridge(EventWriter(self.output), artifact_root=Path(tmpdir))
            bridge.handle({"cmd": "history", "run_id": "does-not-exist"})
        event = self.events()[0]
        self.assertEqual(event["type"], "log")
        self.assertEqual(event["level"], "warning")
        self.assertIn("does-not-exist", event["msg"])


if __name__ == "__main__":
    unittest.main()
