from __future__ import annotations

import difflib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from agents.artifact_manager import ArtifactManager
from agents.historian import DEFAULT_HISTORY_PATH, HistorianAgent
from agents.repo_map_agent import RepoMapAgent
from TUI.repo_renderer import render_repo_architecture, render_repo_tree


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "runs"
DEFAULT_ENTRYPOINTS = {
    "coding_capability": REPO_ROOT / "scripts" / "run_coding_capability.py",
    "worker_limit": REPO_ROOT / "scripts" / "run_worker_limit.py",
    "structured_spec": REPO_ROOT / "scripts" / "run_structured_spec.py",
}


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    entrypoint: str
    kind: str
    status: str
    target: str
    updated_at: str
    attempt_count: int
    resumable: bool = True
    spec_path: str = ""


@dataclass(frozen=True)
class DiffHunk:
    before: str
    after: str
    diff: str
    contract_name: str | None = None


class HarnessDataSource:
    """One JSON/subprocess boundary shared by every TUI screen."""

    def __init__(
        self,
        artifact_root: Path | str = DEFAULT_ARTIFACT_ROOT,
        history_path: Path | str = DEFAULT_HISTORY_PATH,
        repo_root: Path | str = REPO_ROOT,
        python_executable: str = sys.executable,
        entrypoints: Mapping[str, Path | str] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.artifact_manager = ArtifactManager(Path(artifact_root))
        self.historian = HistorianAgent(Path(history_path))
        self.python_executable = python_executable
        self.entrypoints = {
            key: Path(value)
            for key, value in (entrypoints or DEFAULT_ENTRYPOINTS).items()
        }

    @property
    def artifact_root(self) -> Path:
        return self.artifact_manager.root

    def available_entrypoints(self) -> list[str]:
        return sorted(self.entrypoints)

    def available_providers(self) -> list[str]:
        return ["ollama", "deepseek_architect"]

    def list_runs(self) -> list[RunSummary]:
        rows: list[RunSummary] = []
        for run_id in self.artifact_manager.list_runs():
            checkpoint_path = self.artifact_root / run_id / "checkpoint.json"
            try:
                checkpoint = self.artifact_manager.load_checkpoint(run_id) or {}
                stat = checkpoint_path.stat()
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            session = checkpoint.get("session") or checkpoint
            attempts = session.get("attempts") or []
            kind = str(checkpoint.get("kind") or session.get("route") or "controller")
            rows.append(
                RunSummary(
                    run_id=run_id,
                    entrypoint=self._entrypoint_for(run_id, checkpoint),
                    kind=kind,
                    status=str(session.get("final_status") or checkpoint.get("phase") or "running"),
                    target=str(session.get("target") or checkpoint.get("spec_path") or ""),
                    updated_at=datetime.fromtimestamp(
                        stat.st_mtime,
                        tz=timezone.utc,
                    ).isoformat(),
                    attempt_count=len(attempts),
                    spec_path=str(checkpoint.get("spec_path") or ""),
                )
            )
        return rows

    @staticmethod
    def _entrypoint_for(run_id: str, checkpoint: Mapping[str, Any]) -> str:
        if checkpoint.get("kind") == "structured_spec" or run_id.startswith(
            "structured_spec"
        ):
            return "structured_spec"
        if run_id.startswith("worker_limit"):
            return "worker_limit"
        return "coding_capability"

    def load_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        return self.artifact_manager.load_checkpoint(run_id)

    def similar_past_attempts(
        self,
        task_signature: str,
        *,
        limit: int = 3,
        minimum_score: float = 0.25,
    ) -> list[dict[str, Any]]:
        try:
            return self.historian.similar_past_attempts(
                task_signature,
                limit=limit,
                minimum_score=minimum_score,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return []

    def repo_map(
        self,
        root: Path | str | None = None,
        fmt: str = "ascii",
        *,
        focus: str = "",
    ) -> str:
        target = Path(root or self.repo_root).resolve()
        graph = RepoMapAgent().map_repo(target)
        if fmt == "ascii":
            return render_repo_architecture(graph, focus=focus)
        if fmt == "tree":
            return render_repo_tree(graph)
        if fmt == "context":
            return "\n".join(RepoMapAgent().to_plan_context(graph))
        if fmt == "json":
            return json.dumps(asdict(graph), indent=2, sort_keys=True)
        raise ValueError(
            "repo-map format must be one of: ascii, tree, context, json"
        )

    def diff_attempts(
        self,
        run_id: str,
        contract_name: str | None = None,
    ) -> list[DiffHunk]:
        checkpoint = self.load_checkpoint(run_id) or {}
        sources = (
            self._contract_sources(checkpoint, contract_name)
            if contract_name
            else self._attempt_sources(run_id, checkpoint)
        )
        hunks: list[DiffHunk] = []
        for index, (before, after) in enumerate(zip(sources, sources[1:]), start=1):
            diff = "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=f"attempt_{index - 1}.py",
                    tofile=f"attempt_{index}.py",
                )
            )
            hunks.append(
                DiffHunk(
                    before=before,
                    after=after,
                    diff=diff or "(no textual change)\n",
                    contract_name=contract_name,
                )
            )
        return hunks

    def list_change_units(self, run_id: str) -> list[tuple[str, str | None]]:
        """Return source/contract units available to the two-pane diff view."""

        checkpoint = self.load_checkpoint(run_id) or {}
        contracts = [
            (str(result.get("name") or "unnamed contract"), str(result.get("name")))
            for result in checkpoint.get("contract_results", [])
            if result.get("name")
        ]
        if contracts:
            return contracts
        run_dir = self.artifact_root / run_id
        files = sorted(
            run_dir.glob("attempt_*.py"),
            key=lambda path: self._attempt_number(path.stem),
        )
        if files:
            return [(path.name, None) for path in files]
        session = checkpoint.get("session") or checkpoint
        return [
            (f"attempt_{attempt.get('attempt', index)}.py", None)
            for index, attempt in enumerate(session.get("attempts", []))
        ]

    def launch_run(
        self,
        entrypoint: str,
        args: Mapping[str, Any] | None = None,
    ) -> subprocess.Popen[str]:
        command = self.build_command(entrypoint, args or {})
        return subprocess.Popen(
            command,
            cwd=self.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )

    def resume_run(
        self,
        entrypoint: str,
        run_id: str,
        args: Mapping[str, Any] | None = None,
    ) -> subprocess.Popen[str]:
        merged = dict(args or {})
        merged["resume_run"] = run_id
        checkpoint = self.load_checkpoint(run_id) or {}
        if entrypoint == "structured_spec":
            spec_path = checkpoint.get("spec_path")
            if not spec_path:
                raise ValueError(f"Structured-spec checkpoint '{run_id}' has no spec_path")
            merged.setdefault("spec", spec_path)
        return self.launch_run(entrypoint, merged)

    def build_command(
        self,
        entrypoint: str,
        args: Mapping[str, Any],
    ) -> list[str]:
        script = self.entrypoints.get(entrypoint)
        if script is None:
            raise ValueError(f"Unknown entrypoint '{entrypoint}'")
        command = [self.python_executable, str(script)]
        command.extend(["--artifact-root", str(self.artifact_root)])
        command.append("--save-artifacts")

        provider = str(args.get("provider") or "ollama")
        if provider not in self.available_providers():
            raise ValueError(f"Unsupported provider '{provider}'")
        if provider == "deepseek_architect":
            command.extend(["--architect-after-repair-attempts", "1"])

        allowed = {
            "coding_capability": {
                "config": "--config",
                "tasks": "--tasks",
                "runs": "--runs",
                "history": "--history",
                "model": "--model",
                "max_retries": "--max-retries",
                "resume_run": "--resume-run",
            },
            "worker_limit": {
                "tasks": "--tasks",
                "decompositions": "--decompositions",
                "model": "--model",
                "max_retries": "--max-retries",
                "resume_run": "--resume-run",
            },
            "structured_spec": {
                "spec": "--spec",
                "config": "--config",
                "model": "--model",
                "max_retries": "--max-retries",
                "resume_run": "--resume-run",
            },
        }[entrypoint]
        for key, flag in allowed.items():
            value = args.get(key)
            if value is not None and str(value).strip():
                command.extend([flag, str(value)])
        if entrypoint == "structured_spec" and "--spec" not in command:
            raise ValueError("structured_spec requires a spec path")
        return command

    def _attempt_sources(
        self,
        run_id: str,
        checkpoint: Mapping[str, Any],
    ) -> list[str]:
        run_dir = self.artifact_root / run_id
        files = sorted(
            run_dir.glob("attempt_*.py"),
            key=lambda path: self._attempt_number(path.stem),
        )
        if files:
            return [path.read_text(encoding="utf-8") for path in files]
        session = checkpoint.get("session") or checkpoint
        return [
            str(attempt.get("draft") or "")
            for attempt in session.get("attempts", [])
            if attempt.get("draft") is not None
        ]

    def _contract_sources(
        self,
        checkpoint: Mapping[str, Any],
        contract_name: str,
    ) -> list[str]:
        for result in checkpoint.get("contract_results", []):
            if result.get("name") != contract_name:
                continue
            sources = [
                str(item.get("source") or "")
                for item in result.get("repair_attempts", [])
                if item.get("source") is not None
            ]
            final_source = str(result.get("source") or "")
            if final_source and (not sources or sources[-1] != final_source):
                sources.append(final_source)
            return sources
        return []

    @staticmethod
    def _attempt_number(stem: str) -> int:
        try:
            return int(stem.split("_", 1)[1])
        except (IndexError, ValueError):
            return 0
