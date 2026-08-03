import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from harness_kernel.container_sandbox import container_command, run_source_isolated
from harness_kernel.language_adapters import get_language_adapter, supported_languages
from harness_kernel.tool_registry import ToolError


class LanguageAdapterTests(unittest.TestCase):
    def test_supported_languages_and_aliases_are_typed(self) -> None:
        self.assertEqual(
            supported_languages(),
            ("python", "c", "cpp", "rust", "javascript"),
        )
        self.assertEqual(get_language_adapter("c++").language, "cpp")
        self.assertEqual(get_language_adapter("js").filename, "candidate.js")

    def test_unknown_language_is_rejected(self) -> None:
        with self.assertRaises(ToolError) as raised:
            get_language_adapter("brainfuck")
        self.assertEqual(raised.exception.kind, "unsupported_language")


class ContainerSandboxTests(unittest.TestCase):
    def test_container_command_has_hardened_defaults(self) -> None:
        with TemporaryDirectory() as tmpdir:
            command = container_command(Path(tmpdir), "python")

        joined = " ".join(command)
        self.assertIn("--network none", joined)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop ALL", joined)
        self.assertIn("no-new-privileges", command)
        self.assertIn("--pids-limit 64", joined)
        self.assertIn("python:3.11-slim", command)

    def test_missing_container_runtime_never_falls_back_implicitly(self) -> None:
        with patch("harness_kernel.container_sandbox.shutil.which", return_value=None):
            with self.assertRaises(ToolError) as raised:
                run_source_isolated("print('no')", "python", mode="container")
        self.assertEqual(raised.exception.kind, "container_unavailable")

    def test_explicit_local_mode_executes_python_with_sanitized_environment(self) -> None:
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "secret"}, clear=False):
            result = run_source_isolated(
                "import os\nprint(os.environ.get('DEEPSEEK_API_KEY', 'missing'))\n",
                "python",
                mode="local",
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "missing")
        self.assertEqual(result.mode, "local")

    def test_runner_receives_container_command_without_shell_expansion(self) -> None:
        calls = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, b"ok\n", b"")

        with patch("harness_kernel.container_sandbox.shutil.which", return_value="/usr/bin/docker"):
            result = run_source_isolated(
                "print('ok')",
                "python",
                mode="container",
                runner=fake_runner,
            )

        self.assertEqual(result.stdout, "ok\n")
        self.assertIsInstance(calls[0][0], list)
        self.assertNotIn("shell", calls[0][1])
