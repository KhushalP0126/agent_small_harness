import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.artifact_manager import ArtifactManager
from TUI.app import HarnessTUI, RUNTIME_REPAIR_COPY, RunLauncherScreen, _attempt_lines
from TUI.data_source import HarnessDataSource


class ArtifactRunListingTests(unittest.TestCase):
    def test_list_runs_returns_checkpointed_runs_most_recent_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ArtifactManager(tmp)
            older = manager.create_run("older")
            newer = manager.create_run("newer")
            manager.checkpoint({"session": {"attempts": []}}, older)
            manager.checkpoint({"session": {"attempts": []}}, newer)
            os.utime(older.run_dir / "checkpoint.json", (1, 1))
            os.utime(newer.run_dir / "checkpoint.json", (2, 2))

            self.assertEqual(manager.list_runs(), ["newer", "older"])

    def test_list_runs_handles_missing_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ArtifactManager(Path(tmp) / "missing").list_runs(), [])


class HarnessDataSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.artifacts = self.root / "artifacts"
        self.history = self.root / "history.json"
        self.history.write_text(
            json.dumps(
                {
                    "generations": [],
                    "repair_outcomes": [],
                    "lessons_learned": [],
                }
            ),
            encoding="utf-8",
        )
        self.source = HarnessDataSource(
            artifact_root=self.artifacts,
            history_path=self.history,
            repo_root=self.root,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _checkpoint(self, run_id: str, payload: dict) -> None:
        paths = self.source.artifact_manager.create_run(run_id)
        self.source.artifact_manager.checkpoint(payload, paths)

    def test_run_summaries_include_entrypoint_and_session_status(self) -> None:
        self._checkpoint(
            "worker_limit_1_example",
            {
                "session": {
                    "target": "example",
                    "final_status": "completed",
                    "attempts": [{"attempt": 0}],
                }
            },
        )

        [run] = self.source.list_runs()

        self.assertEqual(run.entrypoint, "worker_limit")
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.attempt_count, 1)

    def test_diff_attempts_uses_saved_sources(self) -> None:
        self._checkpoint(
            "run-one",
            {
                "session": {
                    "attempts": [
                        {"draft": "def value():\n    return 1\n"},
                        {"draft": "def value():\n    return 2\n"},
                    ]
                }
            },
        )

        [hunk] = self.source.diff_attempts("run-one")

        self.assertIn("-    return 1", hunk.diff)
        self.assertIn("+    return 2", hunk.diff)
        self.assertEqual(
            self.source.list_change_units("run-one"),
            [("attempt_0.py", None), ("attempt_1.py", None)],
        )

    def test_contract_diff_uses_repair_attempts_and_final_source(self) -> None:
        self._checkpoint(
            "structured_spec_example",
            {
                "kind": "structured_spec",
                "spec_path": "spec.md",
                "contract_results": [
                    {
                        "name": "parse",
                        "repair_attempts": [
                            {"source": "def parse():\n    return 1\n"}
                        ],
                        "source": "def parse():\n    return 2\n",
                    }
                ],
            },
        )

        [hunk] = self.source.diff_attempts(
            "structured_spec_example",
            contract_name="parse",
        )

        self.assertEqual(hunk.contract_name, "parse")
        self.assertIn("+    return 2", hunk.diff)
        self.assertEqual(
            self.source.list_change_units("structured_spec_example"),
            [("parse", "parse")],
        )

    def test_build_command_is_allowlisted_and_requires_structured_spec(self) -> None:
        command = self.source.build_command(
            "structured_spec",
            {
                "spec": "examples/spec.md",
                "model": "qwen2.5-coder:1.5b",
                "provider": "deepseek_architect",
                "ignored": "not-forwarded",
            },
        )

        self.assertIn("--save-artifacts", command)
        self.assertIn("--architect-after-repair-attempts", command)
        self.assertIn("examples/spec.md", command)
        self.assertNotIn("not-forwarded", command)
        with self.assertRaises(ValueError):
            self.source.build_command("structured_spec", {})
        with self.assertRaises(ValueError):
            self.source.build_command("unknown", {})

    def test_resume_structured_spec_infers_original_spec_path(self) -> None:
        self._checkpoint(
            "structured_spec_example",
            {
                "kind": "structured_spec",
                "spec_path": "examples/spec.md",
                "contract_results": [],
            },
        )
        process = MagicMock()
        with patch("TUI.data_source.subprocess.Popen", return_value=process) as popen:
            returned = self.source.resume_run(
                "structured_spec",
                "structured_spec_example",
            )

        self.assertIs(returned, process)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--spec") + 1], "examples/spec.md")
        self.assertEqual(
            command[command.index("--resume-run") + 1],
            "structured_spec_example",
        )

    def test_repo_map_supports_mermaid_context_and_json(self) -> None:
        (self.root / "sample.py").write_text(
            "import json\n\ndef load(value):\n    return json.loads(value)\n",
            encoding="utf-8",
        )

        self.assertTrue(self.source.repo_map(fmt="mermaid").startswith("flowchart LR"))
        self.assertIn("REPO MAP", self.source.repo_map(fmt="context"))
        self.assertIn('"files"', self.source.repo_map(fmt="json"))


class TUIRenderingTests(unittest.TestCase):
    def test_runtime_repair_copy_preserves_static_rescan_claim(self) -> None:
        lines = _attempt_lines(
            {
                "session": {
                    "attempts": [
                        {
                            "attempt": 1,
                            "draft_source_worker": "small_worker",
                            "validation": {
                                "is_compliant": True,
                                "violations": [],
                            },
                            "behavior_validation": {
                                "is_compliant": False,
                                "issues": [{"case": "edge"}],
                            },
                            "formal_validation": {
                                "is_compliant": True,
                                "skipped": True,
                                "issues": [],
                            },
                            "retry_prompt": "repair runtime mismatch",
                        }
                    ]
                }
            }
        )

        self.assertIn(RUNTIME_REPAIR_COPY, lines)
        self.assertNotIn("static engines disabled", "\n".join(lines).lower())


class TextualAppSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_mounts_launcher_with_data_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "history.json"
            history.write_text(
                '{"generations":[],"repair_outcomes":[],"lessons_learned":[]}',
                encoding="utf-8",
            )
            source = HarnessDataSource(
                artifact_root=root / "artifacts",
                history_path=history,
                repo_root=root,
            )
            app = HarnessTUI(source)

            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertIsInstance(app.screen, RunLauncherScreen)
                self.assertEqual(
                    app.screen.query_one("#entrypoint").value,
                    "coding_capability",
                )


if __name__ == "__main__":
    unittest.main()
