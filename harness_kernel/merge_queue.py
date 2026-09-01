"""Serialized, reviewed application of isolated workspace proposals."""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from harness_kernel.checkpoints import Checkpoint, CheckpointStore
from harness_kernel.governance import PermissionEvaluator
from harness_kernel.task_graph import TaskNode, safe_repo_path


@dataclass(frozen=True)
class MergeProposal:
    node_id: str
    workspace: Path
    paths: tuple[str, ...]
    original_hashes: dict[str, str]
    trusted_test_refs: tuple[str, ...] = ()


TrustedValidator = Callable[[Path, tuple[str, ...]], bool]


class SerializedMergeQueue:
    def __init__(self, repository: Path, checkpoints: CheckpointStore,
                 permissions: PermissionEvaluator) -> None:
        self.repository = repository.resolve()
        self.checkpoints = checkpoints
        self.permissions = permissions
        self._queue: list[MergeProposal] = []

    def enqueue(self, node: TaskNode, workspace: Path, original_hashes: dict[str, str]) -> None:
        if not node.edits:
            raise ValueError("read-only nodes cannot propose a merge")
        paths = tuple(safe_repo_path(path) for path in node.write_paths)
        if set(paths) != set(original_hashes):
            raise ValueError("original hashes must cover every claimed write path")
        self._queue.append(MergeProposal(node.node_id, workspace.resolve(), paths,
                                         dict(original_hashes), node.trusted_tests))

    @property
    def pending(self) -> tuple[MergeProposal, ...]:
        return tuple(self._queue)

    def review_next(self, *, approved: bool, session_id: str,
                    validate: TrustedValidator) -> Checkpoint | None:
        if not self._queue:
            raise IndexError("merge queue is empty")
        proposal = self._queue.pop(0)
        if not approved:
            return None
        decision = self.permissions.evaluate("write", f"merge:{proposal.node_id}")
        if not decision.allowed and not decision.approval_required:
            raise PermissionError(decision.reason)
        for path, expected in proposal.original_hashes.items():
            current = _hash(self.repository / path)
            if current != expected:
                raise RuntimeError(f"stale merge proposal for {path}")
        if not validate(proposal.workspace, proposal.trusted_test_refs):
            raise RuntimeError("trusted validation rejected merge proposal")
        checkpoint = self.checkpoints.create(session_id, list(proposal.paths))
        for relative in proposal.paths:
            source = proposal.workspace / relative
            destination = self.repository / relative
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            elif destination.exists():
                destination.unlink()
        return checkpoint


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
