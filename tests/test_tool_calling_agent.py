import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.tool_calling_agent import ToolCallingAgent, _bounded_transcript, _compact_tool_value
from harness_kernel.tool_handlers import build_default_tool_registry


class ToolCallingAgentTests(unittest.TestCase):
    def test_bounded_transcript_keeps_valid_json_and_marks_omitted_turns(self) -> None:
        transcript = [
            {"assistant": {"action": "tool", "turn": index}, "tool_result": {"value": "x" * 80}}
            for index in range(5)
        ]

        rendered = _bounded_transcript(transcript, max_chars=180)
        parsed = json.loads(rendered)

        self.assertEqual(parsed[0]["note"], "4 earlier turn(s) omitted for space")
        self.assertEqual(parsed[-1]["assistant"]["turn"], 4)

    def test_turn_budget_override_is_used_for_one_run(self) -> None:
        response = json.dumps(
            {
                "action": "tool",
                "tool": "search_directory",
                "arguments": {"root": ".", "pattern": "*.py"},
            }
        )
        with TemporaryDirectory() as tmpdir:
            run = ToolCallingAgent(
                lambda _prompt: response,
                build_default_tool_registry(repository_root=Path(tmpdir)),
                max_turns=8,
            ).run("Keep searching", max_turns_override=2)

        self.assertTrue(run.exhausted)
        self.assertEqual(len(run.calls), 2)

    def test_repeated_tool_call_is_reported_to_model(self) -> None:
        response = json.dumps(
            {
                "action": "tool",
                "tool": "search_directory",
                "arguments": {"root": ".", "pattern": "*.py"},
            }
        )
        prompts: list[str] = []
        with TemporaryDirectory() as tmpdir:
            run = ToolCallingAgent(
                lambda prompt: (prompts.append(prompt), response)[1],
                build_default_tool_registry(repository_root=Path(tmpdir)),
                max_turns=2,
            ).run("Keep searching")

        self.assertEqual(run.calls[1].result["error_kind"], "repeated_tool_call")
        self.assertIn("Never repeat an identical tool call", prompts[0])

    def test_large_tool_values_are_compacted(self) -> None:
        compact = _compact_tool_value({"content": "x" * 5000, "path": "main.py"})
        self.assertLess(len(compact["content"]), 5000)
        self.assertEqual(compact["path"], "main.py")

    def test_doc_edit_finalizes_after_redundant_same_file_verification(self) -> None:
        responses = iter(
            [
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "apply_search_replace",
                        "arguments": {
                            "root": ".",
                            "path": "README.md",
                            "search": "old command",
                            "replace": "new command",
                        },
                    }
                ),
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "read_file",
                        "arguments": {"root": ".", "path": "README.md"},
                    }
                ),
            ]
        )
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "README.md").write_text("old command\n", encoding="utf-8")
            run = ToolCallingAgent(
                lambda _prompt: next(responses),
                build_default_tool_registry(repository_root=root),
            ).run("Correct one outdated command")

        self.assertFalse(run.exhausted)
        self.assertIn("diff is prepared", run.final_answer)

    def test_new_file_proposal_finalizes_after_redundant_verification(self) -> None:
        responses = iter(
            [
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "create_file",
                        "arguments": {
                            "root": ".",
                            "path": "docs/notes.md",
                            "content": "# Notes\n",
                        },
                    }
                ),
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "search_directory",
                        "arguments": {"root": ".", "pattern": "docs/notes.md"},
                    }
                ),
            ]
        )
        with TemporaryDirectory() as tmpdir:
            run = ToolCallingAgent(
                lambda _prompt: next(responses),
                build_default_tool_registry(repository_root=Path(tmpdir)),
            ).run("Create documentation notes")

        self.assertFalse(run.exhausted)
        self.assertIn("diff is prepared", run.final_answer)

    def test_model_can_search_read_and_finish_across_turns(self) -> None:
        responses = iter(
            [
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "search_directory",
                        "arguments": {"root": ".", "pattern": "*.py"},
                    }
                ),
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "read_file",
                        "arguments": {"root": ".", "path": "main.py"},
                    }
                ),
                json.dumps({"action": "final", "answer": "main.py returns 1"}),
            ]
        )
        prompts: list[str] = []

        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return next(responses)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            run = ToolCallingAgent(
                generate,
                build_default_tool_registry(repository_root=root),
            ).run("Inspect the entrypoint")

        self.assertEqual(run.final_answer, "main.py returns 1")
        self.assertEqual([call.tool for call in run.calls], ["search_directory", "read_file"])
        self.assertIn("main.py", prompts[-1])
        self.assertFalse(run.exhausted)

    def test_search_replace_result_is_review_only_and_content_is_not_replayed(self) -> None:
        responses = iter(
            [
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "apply_search_replace",
                        "arguments": {
                            "root": ".",
                            "path": "main.py",
                            "search": "return 1",
                            "replace": "return 2",
                        },
                    }
                ),
                json.dumps({"action": "final", "answer": "Diff ready for approval"}),
            ]
        )
        prompts: list[str] = []

        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return next(responses)

        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "main.py"
            path.write_text("def main():\n    return 1\n", encoding="utf-8")
            run = ToolCallingAgent(
                generate,
                build_default_tool_registry(repository_root=root),
            ).run("Prepare the requested change")
            unchanged = path.read_text(encoding="utf-8")

        self.assertEqual(unchanged, "def main():\n    return 1\n")
        self.assertIn("+    return 2", prompts[-1])
        self.assertIn("held for approval", prompts[-1])
        self.assertFalse(run.calls[0].result["value"]["applied"])

    def test_turn_limit_stops_nonterminating_tool_calls(self) -> None:
        response = json.dumps(
            {
                "action": "tool",
                "tool": "search_directory",
                "arguments": {"root": ".", "pattern": "*.py"},
            }
        )
        with TemporaryDirectory() as tmpdir:
            run = ToolCallingAgent(
                lambda _prompt: response,
                build_default_tool_registry(repository_root=Path(tmpdir)),
                max_turns=2,
            ).run("Keep searching")

        self.assertTrue(run.exhausted)
        self.assertEqual(len(run.calls), 2)

    def test_invalid_arguments_are_returned_to_the_model(self) -> None:
        responses = iter(
            [
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "read_file",
                        "arguments": {"path": "main.py", "max_bytes": "not-a-number"},
                    }
                ),
                json.dumps({"action": "final", "answer": "bad arguments reported"}),
            ]
        )
        prompts: list[str] = []

        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return next(responses)

        with TemporaryDirectory() as tmpdir:
            run = ToolCallingAgent(
                generate,
                build_default_tool_registry(repository_root=Path(tmpdir)),
            ).run("Read main.py")

        self.assertEqual(run.calls[0].result["error_kind"], "invalid_arguments")
        self.assertIn("invalid_arguments", prompts[-1])

    def test_result_callback_receives_raw_diff_without_replaying_it_to_model(self) -> None:
        responses = iter(
            [
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "apply_search_replace",
                        "arguments": {
                            "root": ".",
                            "path": "main.py",
                            "search": "return 1",
                            "replace": "return 2",
                        },
                    }
                ),
                json.dumps({"action": "final", "answer": "review it"}),
            ]
        )
        callbacks = []
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            ToolCallingAgent(
                lambda _prompt: next(responses),
                build_default_tool_registry(repository_root=root),
                on_tool_result=lambda record, raw: callbacks.append((record, raw)),
            ).run("prepare a change")

        self.assertEqual(callbacks[0][0].tool, "apply_search_replace")
        self.assertEqual(callbacks[0][1].path, "main.py")
        self.assertIn("return 2", callbacks[0][1].proposed_content)


if __name__ == "__main__":
    unittest.main()
