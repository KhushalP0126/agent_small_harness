import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.artifact_manager import ArtifactManager
from harness_kernel.event_stream import EVENT_FD_ENV, event_sink_from_env
from harness_kernel.tui_bridge import (
    Bridge,
    EventWriter,
    _architect_error_event,
    _contract_event_from_line,
    _dotenv_values,
    _validated_args,
)


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

    def test_repo_map_files_are_structured_per_file(self) -> None:
        self.bridge.handle(
            {"cmd": "repo_map", "root": ".", "focus": "", "mode": "files"}
        )
        event = self.events()[0]
        self.assertEqual(event["type"], "repo_map_files")
        self.assertGreater(len(event["entries"]), 1)
        self.assertEqual(
            set(event["entries"][0]),
            {"path", "summary", "symbols"},
        )

    def test_repo_map_variables_are_structured_per_file(self) -> None:
        self.bridge.handle(
            {"cmd": "repo_map", "root": ".", "focus": "", "mode": "variables"}
        )
        event = self.events()[0]
        self.assertEqual(event["type"], "repo_map_variables")
        self.assertGreater(len(event["entries"]), 1)
        self.assertEqual(
            set(event["entries"][0]),
            {"path", "imports", "variables"},
        )

    def test_argument_allowlist_rejects_unknown_flag(self) -> None:
        with self.assertRaises(ValueError):
            _validated_args(["--not-real", "value"])

    def test_dotenv_values_support_export_and_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "# local settings\nexport DEEPSEEK_API_KEY='secret'\nEMPTY=\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _dotenv_values(env_file),
                {"DEEPSEEK_API_KEY": "secret", "EMPTY": ""},
            )

    def test_child_environment_loads_dotenv_without_overwriting_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "DEEPSEEK_API_KEY=from-dotenv\nARCHITECT_MODEL=from-file\n",
                encoding="utf-8",
            )
            bridge = Bridge(EventWriter(self.output), env_file=env_file)
            with patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "from-shell"},
                clear=True,
            ):
                child_env = bridge._child_environment()
            self.assertEqual(child_env["DEEPSEEK_API_KEY"], "from-shell")
            self.assertEqual(child_env["ARCHITECT_MODEL"], "from-file")

    def test_startup_warning_when_deepseek_is_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = Bridge(
                EventWriter(self.output),
                env_file=Path(tmpdir) / ".env",
            )
            with patch.dict(os.environ, {}, clear=True):
                bridge.emit_startup_warnings()
        event = self.events()[0]
        self.assertEqual(event["level"], "warning")
        self.assertIn("DEEPSEEK_API_KEY", event["msg"])

    def test_architect_error_payload_becomes_high_visibility_log(self) -> None:
        event = _architect_error_event(
            {"architect_contract_error": "architect API key not configured"}
        )
        self.assertEqual(event["type"], "log")
        self.assertEqual(event["level"], "error")
        self.assertIn("DeepSeek unavailable", event["msg"])
        self.assertIn("DEEPSEEK_API_KEY", event["msg"])

    def test_forward_run_surfaces_error_from_multiline_json_summary(self) -> None:
        process = MagicMock()
        process.stdout = io.StringIO(
            json.dumps(
                {
                    "status": "manual_review_required",
                    "architect_contract_error": "architect API key not configured",
                },
                indent=2,
            )
            + "\n"
        )
        process.wait.return_value = 1
        self.bridge._forward_run(process)
        errors = [
            event
            for event in self.events()
            if event.get("type") == "log" and event.get("level") == "error"
        ]
        self.assertEqual(len(errors), 1)
        self.assertIn("DeepSeek unavailable", errors[0]["msg"])
        self.assertEqual(self.events()[-1], {"type": "done", "status": "failed"})

    def test_contract_plan_line_becomes_typed_event(self) -> None:
        event = _contract_event_from_line(
            '[contract-plan] {"contracts":[{"name":"parse","signature":"def parse()"}]}'
        )
        self.assertEqual(event["type"], "contract_queue_planned")
        self.assertEqual(event["contracts"][0]["name"], "parse")

    def test_contract_progress_line_becomes_typed_event(self) -> None:
        event = _contract_event_from_line(
            "[contract-queue] 2/7 parse: retry 1 with small worker"
        )
        self.assertEqual(
            event,
            {
                "type": "contract_progress",
                "name": "parse",
                "status": "retrying",
                "attempt": 1,
                "worker": "small_worker",
            },
        )

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
