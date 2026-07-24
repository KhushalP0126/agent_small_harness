import tempfile
import unittest
from pathlib import Path

from agents.artifact_manager import ArtifactManager
from agents.generation_controller import GenerationController


BAD_SOURCE = """
def analyze(value):
    if value:
        return value
    return 0
"""
GOOD_SOURCE = "def analyze(value):\n    return value\n"


class CheckpointResumeTests(unittest.TestCase):
    def test_artifact_manager_round_trips_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArtifactManager(tmpdir)
            paths = manager.create_run(run_id="resume-test")
            payload = {"session": {"attempts": [{"attempt": 0}]}, "runtime": {"draft": "x"}}
            checkpoint_path = manager.checkpoint(payload, paths)

            self.assertEqual(checkpoint_path.name, "checkpoint.json")
            self.assertEqual(manager.load_checkpoint(paths.run_id), payload)
            self.assertIsNone(manager.load_checkpoint("missing"))

    def test_controller_resumes_from_next_unfinished_attempt(self) -> None:
        snapshots = []
        repair_calls = []

        def checkpoint_writer(payload: dict) -> None:
            snapshots.append(payload)
            if payload["runtime"]["phase"] == "ready_to_validate":
                raise RuntimeError("simulated process interruption")

        first = GenerationController(
            max_retries=2,
            draft_supplier=lambda _prompt: BAD_SOURCE,
            repair_supplier=lambda _draft, _prompt: (
                repair_calls.append("repair") or GOOD_SOURCE
            ),
            policy={"max_cyclomatic_complexity": 1},
            checkpoint_writer=checkpoint_writer,
        )
        with self.assertRaisesRegex(RuntimeError, "simulated process interruption"):
            first.run(target="resume", initial_prompt="generate")

        checkpoint = snapshots[-1]
        self.assertEqual(checkpoint["runtime"]["phase"], "ready_to_validate")
        self.assertEqual(checkpoint["runtime"]["next_attempt"], 1)
        self.assertEqual(len(checkpoint["session"]["attempts"]), 1)

        resumed = GenerationController(
            max_retries=2,
            draft_supplier=lambda _prompt: self.fail("initial supplier must not rerun"),
            repair_supplier=lambda _draft, _prompt: self.fail("repair must not repeat"),
            policy={"max_cyclomatic_complexity": 1},
        ).run(
            target="resume",
            initial_prompt="generate",
            resume_from=checkpoint,
        )

        self.assertEqual(repair_calls, ["repair"])
        self.assertEqual(resumed.payload["final_status"], "completed")
        self.assertEqual(
            [attempt["attempt"] for attempt in resumed.payload["attempts"]],
            [0, 1],
        )


if __name__ == "__main__":
    unittest.main()
