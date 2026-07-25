import json
import tempfile
import unittest
from pathlib import Path

from agents.artifact_manager import ArtifactManager
from agents.generation_controller import GenerationController
from scripts.run_coding_capability import (
    DEFAULT_TASKS as CAPABILITY_TASKS,
    run_tasks,
)
from scripts.run_worker_limit import (
    DEFAULT_DECOMPOSITIONS,
    DEFAULT_TASKS as WORKER_TASKS,
    run_ladder,
)


BAD_SOURCE = """
def analyze(value):
    if value:
        return value
    return 0
"""
GOOD_SOURCE = "def analyze(value):\n    return value\n"


def _terminal_checkpoint(target: str) -> dict:
    return {
        "version": 1,
        "session": {
            "target": target,
            "route": "repair_loop",
            "max_retries": 2,
            "attempts": [],
            "final_status": "completed",
            "human_review": None,
        },
        "runtime": {
            "phase": "terminal",
            "draft": GOOD_SOURCE,
            "previous_draft": BAD_SOURCE,
            "draft_source_worker": "small_worker",
            "next_attempt": 1,
            "architect_repair_retry_used": False,
        },
    }


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

    def test_coding_capability_runner_loads_checkpoint_by_run_id(self) -> None:
        task = json.loads(CAPABILITY_TASKS.read_text(encoding="utf-8"))[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            manager = ArtifactManager(root)
            paths = manager.create_run(run_id="capability-resume")
            manager.checkpoint(_terminal_checkpoint(task["prompt"]), paths)

            exit_code = run_tasks(
                tasks_path=CAPABILITY_TASKS,
                runs_path=Path(tmpdir) / "runs.jsonl",
                history_path=Path(tmpdir) / "history.json",
                artifact_root=root,
                model="unused",
                max_retries=2,
                supplier_mode="fixture",
                record_runs=False,
                save_artifacts=False,
                resume_run_id=paths.run_id,
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue((paths.run_dir / "session_summary.json").is_file())
            self.assertFalse((root / "capability-resume-2").exists())

    def test_worker_limit_runner_loads_checkpoint_by_run_id(self) -> None:
        task = json.loads(WORKER_TASKS.read_text(encoding="utf-8"))[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runs"
            manager = ArtifactManager(root)
            paths = manager.create_run(run_id="worker-resume")
            manager.checkpoint(_terminal_checkpoint(task["prompt"]), paths)

            exit_code = run_ladder(
                tasks_path=WORKER_TASKS,
                decompositions_path=DEFAULT_DECOMPOSITIONS,
                artifact_root=root,
                model="unused",
                max_retries=2,
                num_ctx=512,
                num_predict=64,
                save_artifacts=False,
                continue_after_failure=False,
                decompose=False,
                architect_after_repair_attempts=None,
                debug_controller=False,
                resume_run_id=paths.run_id,
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue((paths.run_dir / "session_summary.json").is_file())

    def test_makefile_exposes_resume_targets(self) -> None:
        makefile = Path("Makefile").read_text(encoding="utf-8")
        self.assertIn("resume-coding-capability:", makefile)
        self.assertIn("resume-worker-limit:", makefile)
        self.assertIn("resume-structured-spec:", makefile)
        self.assertIn('--resume-run "$(RESUME_RUN)"', makefile)

    def test_ci_installs_formal_extras(self) -> None:
        workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn('python -m pip install -e ".[formal]"', workflow)


if __name__ == "__main__":
    unittest.main()
