import tempfile
import unittest
from pathlib import Path

from harness_kernel.checkpoints import CheckpointStore


class CheckpointTests(unittest.TestCase):
    def test_create_restore_delete_and_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = root / "repo"; repo.mkdir(); state = root / "state"
            target = repo / "a.txt"; target.write_text("before")
            store = CheckpointStore(repo, state)
            first = store.create("session", ["a.txt", "new.txt"], conversation_summary="goal")
            target.write_text("after"); (repo / "new.txt").write_text("new")
            restored = store.restore(first.checkpoint_id)
            self.assertEqual(target.read_text(), "before")
            self.assertFalse((repo / "new.txt").exists())
            self.assertEqual(restored.conversation_summary, "goal")
            self.assertTrue(store.branch(first.checkpoint_id).startswith("session-branch-"))

    def test_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = root / "repo"; repo.mkdir()
            with self.assertRaises(ValueError): CheckpointStore(repo, root / "state").create("s", ["../outside"])

    def test_restore_rejects_checkpoint_id_traversal_and_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = root / "repo"; repo.mkdir(); state = root / "state"
            (repo / "a.txt").write_text("before")
            store = CheckpointStore(repo, state)
            checkpoint = store.create("s", [str(repo / "a.txt")])
            self.assertEqual(checkpoint.changed_paths, ("a.txt",))
            with self.assertRaises(ValueError):
                store.restore("../outside")
            (state / checkpoint.checkpoint_id / "content" / "a.txt").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                store.restore(checkpoint.checkpoint_id)


if __name__ == "__main__": unittest.main()
