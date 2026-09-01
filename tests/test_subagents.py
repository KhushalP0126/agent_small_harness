import tempfile
import unittest
from pathlib import Path

from harness_kernel.subagents import IsolatedSubagentWorkspace


class SubagentIsolationTests(unittest.TestCase):
    def test_edits_are_isolated_and_merge_requires_review(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"; repo.mkdir()
            shared = repo / "main.py"; shared.write_text("value = 1\n")
            with IsolatedSubagentWorkspace(repo) as workspace:
                (workspace.path / "main.py").write_text("value = 2\n")
                proposal = workspace.diff(["python compile: pass"])
                self.assertEqual(shared.read_text(), "value = 1\n")
                self.assertFalse(workspace.merge_reviewed(proposal, approved=False))
                self.assertTrue(workspace.merge_reviewed(proposal, approved=True))
            self.assertEqual(shared.read_text(), "value = 2\n")
            self.assertEqual(proposal.validation_evidence, ("python compile: pass",))

    def test_stale_shared_workspace_blocks_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"; repo.mkdir()
            shared = repo / "main.py"; shared.write_text("value = 1\n")
            with IsolatedSubagentWorkspace(repo) as workspace:
                (workspace.path / "main.py").write_text("value = 2\n")
                proposal = workspace.diff([])
                shared.write_text("value = 3\n")
                with self.assertRaisesRegex(ValueError, "changed after"):
                    workspace.merge_reviewed(proposal, approved=True)


if __name__ == "__main__": unittest.main()
