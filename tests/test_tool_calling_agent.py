import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.tool_calling_agent import ToolCallingAgent, _bounded_transcript
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
