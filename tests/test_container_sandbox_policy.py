from __future__ import annotations

import unittest
from pathlib import Path

from harness_kernel.container_sandbox import (
    CONTAINER_DEFAULT_NETWORK,
    HOST_FS_ALLOWLIST,
    container_command,
)


class ContainerSandboxPolicyTests(unittest.TestCase):
    def test_default_network_is_none(self) -> None:
        self.assertEqual(CONTAINER_DEFAULT_NETWORK, "none")
        cmd = container_command(Path("/tmp/scratch"), "python")
        # network flag followed by none
        idx = cmd.index("--network")
        self.assertEqual(cmd[idx + 1], "none")

    def test_read_only_root_and_workspace_mount(self) -> None:
        scratch = Path("/tmp/agent-scratch-test")
        cmd = container_command(scratch, "python")
        self.assertIn("--read-only", cmd)
        self.assertIn("--cap-drop", cmd)
        mount = next(item for item in cmd if item.startswith("type=bind"))
        self.assertIn("dst=/workspace", mount)
        self.assertTrue(set(HOST_FS_ALLOWLIST) >= {"/workspace", "/tmp"})

    def test_network_can_be_enabled_explicitly(self) -> None:
        cmd = container_command(Path("/tmp/scratch"), "python", network_enabled=True)
        idx = cmd.index("--network")
        self.assertEqual(cmd[idx + 1], "bridge")


if __name__ == "__main__":
    unittest.main()
