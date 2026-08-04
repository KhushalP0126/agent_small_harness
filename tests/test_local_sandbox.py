import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_kernel.local_sandbox import (
    MAX_CAPTURE_BYTES,
    run_python_locally_isolated,
    run_python_project_locally_isolated,
)


class LocalSandboxTests(unittest.TestCase):
    def test_child_gets_disposable_directory_without_parent_secrets(self) -> None:
        source = """
import json
import os

print(json.dumps({
    "secret": os.environ.get("DEEPSEEK_API_KEY"),
    "cwd": os.getcwd(),
    "home": os.environ.get("HOME"),
}))
"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "must-not-leak"}):
            result = run_python_locally_isolated(source, timeout_seconds=1.0)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIsNone(payload["secret"])
        self.assertEqual(Path(payload["cwd"]).resolve(), Path(payload["home"]).resolve())
        self.assertEqual(
            Path(payload["cwd"]).resolve(),
            Path(result.working_directory).resolve(),
        )
        self.assertFalse(Path(result.working_directory).exists())

    def test_timeout_terminates_busy_generated_code(self) -> None:
        result = run_python_locally_isolated("while True:\n    pass\n", timeout_seconds=0.05)

        self.assertTrue(result.timed_out)
        self.assertIsNotNone(result.returncode)

    def test_captured_output_is_bounded(self) -> None:
        result = run_python_locally_isolated(
            f"print('x' * {MAX_CAPTURE_BYTES * 2})\n",
            timeout_seconds=1.0,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), MAX_CAPTURE_BYTES)

    def test_project_execution_resolves_cross_file_imports(self) -> None:
        result = run_python_project_locally_isolated(
            {
                "helpers.py": "def value():\n    return 7\n",
                "main.py": "from helpers import value\nprint(value())\n",
            },
            entrypoint="main.py",
            timeout_seconds=2,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "7")


if __name__ == "__main__":
    unittest.main()
