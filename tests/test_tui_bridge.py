import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.artifact_manager import ArtifactManager
from agents.plan_mode import PlanModeAgent
from harness_kernel.event_stream import EVENT_FD_ENV, event_sink_from_env
from harness_kernel.tui_bridge import (
    Bridge,
    EventWriter,
    _architect_error_event,
    _action_approval_reason,
    _code_excerpt,
    _contract_event_from_line,
    _dotenv_values,
    _parse_questionnaire_response,
    _render_spec_sheet,
    _should_start_planning,
    _validated_args,
)


def completed_spec_sheet() -> str:
    return json.dumps(
        {
            "app_spec": {
                "name": "task_manager",
                "language": "python",
                "libraries": ["sqlite3"],
                "kernel_mode": "generate_from_spec",
            },
            "goal": "Build a terminal task manager.",
            "files": ["task_manager.py"],
            "required_components": ["Task", "TaskStore", "main()"],
            "entrypoints": ["main()"],
            "dependency_graph": ["Task -> TaskStore", "TaskStore -> main"],
            "state_rules": ["Completed tasks remain persisted."],
            "interfaces": ["Read commands from the terminal."],
            "constraints": ["Use sqlite3 from the standard library."],
            "acceptance_examples": ["Adding a task shows it in the pending list."],
            "validation": ["Run the unit test suite."],
        }
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

    def wait_for_event(self, event_type: str) -> dict:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            for event in self.events():
                if event.get("type") == event_type:
                    return event
            time.sleep(0.01)
        self.fail(f"timed out waiting for {event_type}: {self.events()}")

    def test_unknown_command_is_structured_log(self) -> None:
        self.bridge.handle({"cmd": "missing"})
        self.assertEqual(self.events()[0]["type"], "log")
        self.assertEqual(self.events()[0]["level"], "error")

    def test_tool_task_streams_calls_and_answer(self) -> None:
        responses = iter(
            [
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "search_directory",
                        "arguments": {"root": ".", "pattern": "*.py"},
                    }
                ),
                json.dumps({"action": "final", "answer": "inspection complete"}),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
            bridge = Bridge(
                EventWriter(self.output),
                tool_generate_text=lambda _prompt: next(responses),
                tool_repository_root=root,
            )
            bridge.handle({"cmd": "tool_task", "text": "inspect", "provider": "qwen"})
            answer = self.wait_for_event("tool_answer")

        events = self.events()
        call = next(event for event in events if event["type"] == "tool_call")
        self.assertEqual(call["tool"], "search_directory")
        self.assertTrue(call["ok"])
        self.assertEqual(answer["answer"], "inspection complete")

    def test_check_command_streams_real_structural_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            bridge = Bridge(EventWriter(self.output), tool_repository_root=root)
            bridge.handle({"cmd": "check", "path": "broken.py"})
        event = self.events()[-1]
        self.assertEqual(event["type"], "check_result")
        self.assertFalse(event["passed"])
        self.assertEqual(event["findings"][0]["engine"], "engine-parse-contract")

    def test_context_usage_event_uses_reported_backend_tokens(self) -> None:
        self.bridge._emit_context_usage(
            backend="local",
            model="qwen2.5-coder:1.5b",
            prompt_tokens=3200,
            completion_tokens=896,
            context_window=8192,
            estimated_cost_usd=0.0,
        )
        self.assertEqual(
            self.events(),
            [
                {
                    "type": "context_usage",
                    "backend": "local",
                    "model": "qwen2.5-coder:1.5b",
                    "prompt_tokens": 3200,
                    "completion_tokens": 896,
                    "total_tokens": 4096,
                    "context_window": 8192,
                    "estimated_cost_usd": 0.0,
                }
            ],
        )

    def test_local_generation_emits_ollama_usage(self) -> None:
        class OllamaStub:
            last_usage = {"prompt_tokens": 321, "completion_tokens": 89}

            def generate(self, *_args, **_kwargs) -> str:
                return '{"action":"final","answer":"done"}'

        response = self.bridge._generate_local(
            OllamaStub(),
            "inspect this file",
            system="tool JSON only",
        )

        self.assertIn('"action":"final"', response)
        event = self.events()[-1]
        self.assertEqual(event["type"], "context_usage")
        self.assertEqual(event["prompt_tokens"], 321)
        self.assertEqual(event["completion_tokens"], 89)
        self.assertEqual(event["context_window"], 8192)

    def test_tool_diff_requires_explicit_approval(self) -> None:
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
                json.dumps({"action": "final", "answer": "diff ready"}),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "main.py"
            path.write_text("def main():\n    return 1\n", encoding="utf-8")
            bridge = Bridge(
                EventWriter(self.output),
                tool_generate_text=lambda _prompt: next(responses),
                tool_repository_root=root,
            )
            bridge.handle({"cmd": "tool_task", "text": "change return", "provider": "qwen"})
            diff = self.wait_for_event("tool_diff")
            self.assertEqual(path.read_text(encoding="utf-8"), "def main():\n    return 1\n")
            bridge.handle({"cmd": "apply_tool_diff", "approved": True})
            resolved = self.wait_for_event("tool_diff_resolved")
            self.assertEqual(path.read_text(encoding="utf-8"), "def main():\n    return 2\n")

        self.assertEqual(diff["path"], "main.py")
        self.assertTrue(resolved["applied"])

    def test_chat_filesystem_request_uses_a_reviewed_tool_diff(self) -> None:
        responses = iter(
            [
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "execute_script",
                        "arguments": {"root": ".", "source": "print('wrong tool')"},
                    }
                ),
                json.dumps(
                    {
                        "action": "tool",
                        "tool": "apply_search_replace",
                        "arguments": {
                            "root": ".",
                            "path": "exp/hello.txt",
                            "operation": "create",
                            "search": "",
                            "replace": "hello world\n",
                        },
                    }
                ),
                json.dumps({"action": "final", "answer": "diff ready"}),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "exp" / "hello.txt"
            bridge = Bridge(
                EventWriter(self.output),
                tool_generate_text=lambda _prompt: next(responses),
                tool_repository_root=root,
            )
            bridge.start_chat("Create a directory called exp and add hello world in a text file")
            diff = self.wait_for_event("tool_diff")
            self.assertFalse(path.exists())
            bridge.handle({"cmd": "apply_tool_diff", "approved": True})
            resolved = self.wait_for_event("tool_diff_resolved")

            self.assertEqual(path.read_text(encoding="utf-8"), "hello world\n")
        self.assertEqual(diff["path"], "exp/hello.txt")
        self.assertTrue(resolved["applied"])
        rejected = next(event for event in self.events() if event["type"] == "tool_call")
        self.assertFalse(rejected["ok"])
        self.assertIn("declared tool", rejected["summary"])

    def test_action_approval_identifies_sensitive_repository_requests(self) -> None:
        self.assertIn(
            "GitHub",
            _action_approval_reason("Push this branch to GitHub") or "",
        )
        self.assertIn(
            "Restructuring",
            _action_approval_reason("Refactor the repository modules") or "",
        )
        self.assertIn(
            "Deleting",
            _action_approval_reason("Remove the file obsolete.py") or "",
        )
        self.assertIsNone(_action_approval_reason("Explain the repository layout"))

    def test_rejected_action_approval_does_not_start_tools(self) -> None:
        self.bridge.start_chat("Delete the file obsolete.py")
        approval = self.wait_for_event("action_approval")
        self.assertEqual(approval["request"], "Delete the file obsolete.py")

        self.bridge.handle({"cmd": "approve_action", "approved": False})

        self.assertFalse(any(event["type"] == "tool_call" for event in self.events()))
        self.assertTrue(
            any(
                event["type"] == "log" and "declined" in event["msg"]
                for event in self.events()
            )
        )

    def test_code_excerpt_is_line_numbered_and_bounded(self) -> None:
        source = "\n".join(f"line_{index}" for index in range(40))
        excerpt, start_line, truncated = _code_excerpt(source, max_lines=12)
        self.assertEqual(start_line, 1)
        self.assertTrue(truncated)
        self.assertIn("   1 │ line_0", excerpt)
        self.assertIn("… 30 line(s) omitted …", excerpt)
        self.assertIn("  40 │ line_39", excerpt)

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
                bridge.emit_startup_status()
        events = self.events()
        self.assertEqual(events[0]["type"], "config_status")
        self.assertFalse(events[0]["deepseek_configured"])
        self.assertEqual(events[1]["level"], "warning")
        self.assertIn("DEEPSEEK_API_KEY", events[1]["msg"])

    def test_startup_status_reports_dotenv_source_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("DEEPSEEK_API_KEY=secret-value\n", encoding="utf-8")
            bridge = Bridge(EventWriter(self.output), env_file=env_file)
            with patch.dict(os.environ, {}, clear=True):
                bridge.emit_startup_status()
        event = self.events()[0]
        self.assertTrue(event["deepseek_configured"])
        self.assertEqual(event["source"], ".env:DEEPSEEK_API_KEY")
        self.assertNotIn("secret-value", json.dumps(event))

    def test_architect_error_payload_becomes_high_visibility_log(self) -> None:
        event = _architect_error_event(
            {
                "architect_contract_error_code": "architect_contract_missing_api_key",
                "architect_contract_error": "architect API key not configured",
            }
        )
        self.assertEqual(event["type"], "log")
        self.assertEqual(event["level"], "error")
        self.assertIn("not configured", event["msg"])
        self.assertIn("DEEPSEEK_API_KEY", event["msg"])

    def test_invalid_planner_response_does_not_blame_api_key(self) -> None:
        event = _architect_error_event(
            {
                "architect_contract_error_code": "architect_contract_plan_invalid_json",
                "architect_contract_error": "plan must contain an order",
            }
        )
        self.assertIn("planner error", event["msg"])
        self.assertNotIn("DEEPSEEK_API_KEY", event["msg"])

    def test_invalid_planner_response_warns_when_spec_queue_is_used(self) -> None:
        event = _architect_error_event(
            {
                "architect_contract_error_code": "architect_contract_plan_invalid_json",
                "architect_contract_error": "plan must contain an order",
                "architect_contracts_fallback_used": True,
            }
        )
        self.assertEqual(event["level"], "warning")
        self.assertIn("validated spec-sheet contract queue", event["msg"])

    def test_forward_run_surfaces_error_from_multiline_json_summary(self) -> None:
        process = MagicMock()
        process.stdout = io.StringIO(
            json.dumps(
                {
                    "status": "manual_review_required",
                    "architect_contract_error_code": "architect_contract_missing_api_key",
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
        self.assertIn("not configured", errors[0]["msg"])
        self.assertEqual(self.events()[-1], {"type": "done", "status": "failed"})

    def test_chat_is_non_mutating_and_emits_assistant_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = Bridge(
                EventWriter(self.output),
                memory_path=Path(tmpdir) / "memory.json",
                tool_generate_text=lambda _prompt: json.dumps(
                    {"action": "final", "answer": "Hello. How can I help?"}
                ),
            )
            bridge.handle({"cmd": "chat", "text": "hello"})
            reply = self.wait_for_event("chat_message")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and len(
                [event for event in self.events() if event["type"] == "chat_message"]
            ) < 2:
                time.sleep(0.01)
        messages = [event for event in self.events() if event["type"] == "chat_message"]
        answer = self.wait_for_event("tool_answer")
        self.assertEqual(reply["role"], "user")
        self.assertEqual(len(messages), 1)
        self.assertEqual(answer["answer"], "Hello. How can I help?")
        self.assertIsNone(bridge._process)

    def test_plain_chat_uses_the_same_read_only_tool_loop(self) -> None:
        bridge = Bridge(
            EventWriter(self.output),
            tool_generate_text=lambda _prompt: json.dumps(
                {"action": "final", "answer": "This must stay chat."}
            ),
        )
        bridge._chat_history.append({"role": "user", "content": "hello"})
        bridge._run_chat()

        self.assertFalse(any(event["type"] == "questionnaire" for event in self.events()))
        self.assertEqual(self.events()[-1]["answer"], "This must stay chat.")
        self.assertEqual(
            bridge._chat_history[-1],
            {"role": "assistant", "content": "This must stay chat."},
        )

    def test_planning_intent_is_explicit(self) -> None:
        self.assertTrue(_should_start_planning("Help me plan a terminal app"))
        self.assertTrue(_should_start_planning("I want to build a task manager"))
        self.assertTrue(_should_start_planning("We are planning a CLI tool"))
        self.assertFalse(_should_start_planning("hello"))
        self.assertFalse(_should_start_planning("what does this repository do?"))
        self.assertFalse(_should_start_planning("Plan a vacation to Japan"))
        self.assertFalse(_should_start_planning("Make a presentation for Friday"))

    def test_questionnaire_response_is_normalized_with_other_fallback(self) -> None:
        message, questions = _parse_questionnaire_response(
            json.dumps(
                {
                    "kind": "questionnaire",
                    "message": "A few choices first.",
                    "questions": [
                        {
                            "question_text": "Choose a surface",
                            "options": ["CLI", "TUI", "Other", "TUI"],
                        },
                        {
                            "question_text": "Choose storage",
                            "options": ["JSON", "SQLite"],
                        },
                    ],
                }
            )
        )

        self.assertEqual(message, "A few choices first.")
        self.assertEqual(len(questions), 2)
        self.assertEqual(
            [option["text"] for option in questions[0]["options"]],
            ["CLI", "TUI", "Other"],
        )
        self.assertEqual(questions[1]["options"][-1]["text"], "Other")

    def test_project_chat_emits_typed_questionnaire(self) -> None:
        client = MagicMock()
        client.generate.return_value = json.dumps(
            {
                "kind": "questionnaire",
                "message": "Let me clarify the project.",
                "questions": [
                    {"question_text": "Target?", "options": ["CLI", "Web"]},
                    {"question_text": "Storage?", "options": ["JSON", "SQLite"]},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = Bridge(
                EventWriter(self.output),
                memory_path=Path(tmpdir) / "memory.json",
                architect_client=client,
            )
            bridge.start_chat("Build a task manager")
            event = self.wait_for_event("questionnaire")

        self.assertEqual(len(event["questions"]), 2)
        self.assertEqual(event["questions"][0]["options"][-1]["text"], "Other")
        self.assertIsNone(bridge._process)

    def test_planning_tool_task_uses_the_same_spec_intake(self) -> None:
        client = MagicMock()
        client.generate.return_value = json.dumps(
            {
                "kind": "questionnaire",
                "message": "Let me clarify the project.",
                "questions": [
                    {"question_text": "Target?", "options": ["CLI", "Web"]},
                    {"question_text": "Storage?", "options": ["JSON", "SQLite"]},
                ],
            }
        )
        bridge = Bridge(EventWriter(self.output), architect_client=client)
        bridge.start_tool_task("Build a task manager")
        event = self.wait_for_event("questionnaire")

        self.assertEqual(len(event["questions"]), 2)
        self.assertTrue(
            any(
                item.get("msg") == "planning request routed to spec intake"
                for item in self.events()
                if item["type"] == "log"
            )
        )

    def test_questionnaire_completion_drafts_spec_without_execution(self) -> None:
        client = MagicMock()
        client.generate.return_value = completed_spec_sheet()
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = Bridge(
                EventWriter(self.output),
                memory_path=Path(tmpdir) / "memory.json",
                architect_client=client,
            )
            bridge.complete_questionnaire(
                [
                    {"question_text": "Target?", "answer": "TUI"},
                    {"question_text": "Storage?", "answer": "SQLite"},
                ]
            )
            event = self.wait_for_event("spec_draft")

        self.assertTrue(event["text"].startswith("# Execution Spec Sheet"))
        self.assertIn("## Required Components\n\n- `Task`", event["text"])
        self.assertIn("QUESTIONNAIRE ANSWERS", client.generate.call_args.args[0])
        self.assertIn("Fill every field", client.generate.call_args.args[0])
        self.assertIsNone(bridge._process)

    def test_draft_spec_uses_chat_history_without_executing(self) -> None:
        client = MagicMock()
        client.generate.return_value = completed_spec_sheet()
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = Bridge(
                EventWriter(self.output),
                memory_path=Path(tmpdir) / "memory.json",
                architect_client=client,
                tool_generate_text=lambda _prompt: json.dumps(
                    {"action": "final", "answer": "Let's plan it."}
                ),
            )
            bridge.start_chat("Build a parser")
            self.wait_for_event("assistant_status")
            deadline = time.monotonic() + 2
            while bridge._assistant_busy and time.monotonic() < deadline:
                time.sleep(0.01)
            bridge.start_spec_draft()
            event = self.wait_for_event("spec_draft")
        self.assertTrue(event["text"].startswith("# Execution Spec Sheet"))
        self.assertIsNone(bridge._process)

    def test_completed_spec_sheet_is_parseable_by_plan_mode(self) -> None:
        rendered = _render_spec_sheet(completed_spec_sheet())
        plan = PlanModeAgent().plan(rendered)

        self.assertEqual(plan.app_name, "task_manager")
        self.assertEqual(plan.files, ["task_manager.py"])
        self.assertEqual(plan.components, ["`Task`", "`TaskStore`", "`main()`"])
        self.assertEqual(plan.entrypoints, ["`main()`"])
        self.assertEqual(
            plan.dependency_graph_context,
            ["Task -> TaskStore", "TaskStore -> main"],
        )

    def test_invalid_spec_sheet_is_rejected_before_review(self) -> None:
        with self.assertRaisesRegex(ValueError, "required_components"):
            _render_spec_sheet(
                json.dumps(
                    {
                        **json.loads(completed_spec_sheet()),
                        "required_components": [],
                    }
                )
            )

    def test_execute_spec_is_the_only_command_that_starts_structured_run(self) -> None:
        with patch.object(self.bridge, "start_run") as start_run:
            self.bridge.handle({"cmd": "execute_spec", "text": "# Approved spec"})
        start_run.assert_called_once()
        self.assertEqual(start_run.call_args.args[0], "structured_spec")

    def test_explicit_preference_is_saved_without_sensitive_values(self) -> None:
        client = MagicMock()
        client.generate.return_value = "Understood."
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "memory.json"
            bridge = Bridge(
                EventWriter(self.output),
                memory_path=memory_path,
                architect_client=client,
            )
            bridge.start_chat("remember that keep responses concise")
            event = self.wait_for_event("memory_updated")
            saved = json.loads(memory_path.read_text(encoding="utf-8"))
        self.assertTrue(event["added"])
        self.assertEqual(saved["preferences"], ["keep responses concise"])

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
