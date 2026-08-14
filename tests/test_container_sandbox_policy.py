from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from harness_kernel.container_sandbox import (
    CONTAINER_DEFAULT_NETWORK,
    DEFAULT_CONTAINER_FS_POLICY,
    HOST_FS_ALLOWLIST,
    ContainerFilesystemPolicy,
    ContainerMount,
    container_command,
    enforce_container_only_tool_policy,
    run_source_isolated,
)
from harness_kernel.tool_handlers import ExecuteScriptRequest, build_default_tool_registry
from harness_kernel.tool_registry import ToolError


class ContainerSandboxPolicyTests(unittest.TestCase):
    def test_default_network_is_none(self) -> None:
        self.assertEqual(CONTAINER_DEFAULT_NETWORK, "none")
        with TemporaryDirectory() as tmpdir:
            cmd = container_command(Path(tmpdir), "python")
        idx = cmd.index("--network")
        self.assertEqual(cmd[idx + 1], "none")

    def test_read_only_root_and_workspace_mount(self) -> None:
        with TemporaryDirectory() as tmpdir:
            scratch = Path(tmpdir)
            cmd = container_command(scratch, "python")
        self.assertIn("--read-only", cmd)
        self.assertIn("--cap-drop", cmd)
        mount = next(item for item in cmd if item.startswith("type=bind"))
        self.assertIn("dst=/workspace", mount)
        self.assertIn(f"src={scratch.resolve()}", mount)
        self.assertTrue(set(HOST_FS_ALLOWLIST) >= {"/workspace", "/tmp"})
        tmpfs = cmd[cmd.index("--tmpfs") + 1]
        self.assertTrue(tmpfs.startswith("/tmp:"))
        self.assertIn("noexec", tmpfs)

    def test_network_can_be_enabled_explicitly(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cmd = container_command(Path(tmpdir), "python", network_enabled=True)
        idx = cmd.index("--network")
        self.assertEqual(cmd[idx + 1], "bridge")

    def test_extra_bind_mounts_rejected_by_default(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with self.assertRaises(ToolError) as raised:
                container_command(
                    Path(tmpdir),
                    "python",
                    extra_mounts=[
                        ContainerMount(
                            source=Path(tmpdir),
                            destination="/etc",
                            read_only=True,
                        )
                    ],
                )
        self.assertEqual(raised.exception.kind, "fs_policy_violation")

    def test_non_allowlisted_destination_rejected(self) -> None:
        policy = ContainerFilesystemPolicy(allow_extra_bind_mounts=True)
        with TemporaryDirectory() as tmpdir:
            with self.assertRaises(ToolError) as raised:
                container_command(
                    Path(tmpdir),
                    "python",
                    extra_mounts=[
                        ContainerMount(
                            source=Path(tmpdir),
                            destination="/home/user",
                            read_only=True,
                        )
                    ],
                    policy=policy,
                )
        self.assertEqual(raised.exception.kind, "fs_policy_violation")
        self.assertIn("not allowlisted", str(raised.exception))

    def test_docker_socket_host_source_rejected(self) -> None:
        with self.assertRaises(ToolError) as raised:
            DEFAULT_CONTAINER_FS_POLICY.validate_host_source(Path("/var/run/docker.sock"))
        self.assertEqual(raised.exception.kind, "fs_policy_violation")

    def test_raw_volume_flags_rejected_by_command_assert(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cmd = container_command(Path(tmpdir), "python")
        # Mutate a validated command as a hostile caller might.
        hostile = list(cmd) + ["-v", "/:/host"]
        with self.assertRaises(ToolError) as raised:
            from harness_kernel.container_sandbox import _assert_command_obeys_policy

            _assert_command_obeys_policy(hostile, DEFAULT_CONTAINER_FS_POLICY)
        self.assertEqual(raised.exception.kind, "fs_policy_violation")

    def test_tool_policy_blocks_local_without_approval(self) -> None:
        with self.assertRaises(ToolError) as raised:
            enforce_container_only_tool_policy(mode="local", allow_local_sandbox=False)
        self.assertEqual(raised.exception.kind, "local_sandbox_disabled")

    def test_tool_policy_allows_local_with_approval(self) -> None:
        self.assertEqual(
            enforce_container_only_tool_policy(mode="local", allow_local_sandbox=True),
            "local",
        )

    def test_execute_script_tool_enforces_container_only_and_fs_policy(self) -> None:
        with TemporaryDirectory() as tmpdir:
            registry = build_default_tool_registry(repository_root=Path(tmpdir))
            captured = {}

            def fake_run(source, language, **kwargs):
                captured.update(kwargs)
                captured["source"] = source
                captured["language"] = language
                from harness_kernel.container_sandbox import SandboxResult

                # Ensure the FS policy object is the host default.
                self.assertIs(kwargs.get("policy"), DEFAULT_CONTAINER_FS_POLICY)
                self.assertTrue(kwargs.get("enforce_container_only_tools"))
                self.assertFalse(kwargs.get("network_enabled"))
                return SandboxResult("python", "container", 0, "ok\n", "", False)

            with patch(
                "harness_kernel.tool_handlers.run_source_isolated",
                side_effect=fake_run,
            ):
                result = registry.dispatch(
                    "execute_script",
                    ExecuteScriptRequest(root=Path("."), source="print('ok')"),
                )
        self.assertTrue(result.ok)
        self.assertEqual(captured["mode"], "container")

    def test_execute_script_rejects_local_mode_by_policy(self) -> None:
        with TemporaryDirectory() as tmpdir:
            registry = build_default_tool_registry(repository_root=Path(tmpdir))
            result = registry.dispatch(
                "execute_script",
                ExecuteScriptRequest(
                    root=Path("."),
                    source="print('blocked')",
                    sandbox_mode="local",
                ),
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "local_sandbox_disabled")

    def test_run_source_isolated_still_allows_direct_local_mode(self) -> None:
        result = run_source_isolated("print('direct')\n", "python", mode="local")
        self.assertEqual(result.mode, "local")
        self.assertEqual(result.returncode, 0)

    def test_run_source_isolated_container_builds_policy_command(self) -> None:
        calls = []

        def fake_runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, b"ok\n", b"")

        with patch("harness_kernel.container_sandbox.shutil.which", return_value="/usr/bin/docker"):
            result = run_source_isolated(
                "print('ok')",
                "python",
                mode="container",
                runner=fake_runner,
            )
        self.assertEqual(result.stdout, "ok\n")
        command = calls[0]
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        mount = next(item for item in command if isinstance(item, str) and item.startswith("type=bind"))
        self.assertIn("dst=/workspace", mount)
        self.assertNotIn("-v", command)


if __name__ == "__main__":
    unittest.main()
