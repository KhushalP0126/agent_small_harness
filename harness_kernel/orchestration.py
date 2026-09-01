"""Deterministic concurrent scheduler for approved task graph nodes."""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable

from harness_kernel.event_journal import EventJournal
from harness_kernel.task_graph import GraphRevision, TaskNode


class NodeState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class NodeResult:
    success: bool
    artifacts: tuple[dict, ...] = ()
    message: str = ""


NodeRunner = Callable[[TaskNode, str, Path, Path], Awaitable[NodeResult]]


class OrchestrationSession:
    def __init__(self, revision: GraphRevision, repository: Path, state_root: Path, *,
                 concurrency: int = 3, journal: EventJournal | None = None) -> None:
        if not 1 <= concurrency <= 8:
            raise ValueError("concurrency must be between 1 and 8")
        self.revision = revision
        self.repository = repository.resolve()
        self.state_root = state_root.resolve()
        self.concurrency = concurrency
        self.journal = journal
        self.approved_hash: str | None = None
        self.states = {node.node_id: NodeState.PENDING for node in revision.graph.nodes}
        self.providers: dict[str, str] = {}
        self.merge_queue: list[str] = []
        self.proposals: dict[str, Path] = {}
        self.breakpoints: set[str] = set()
        self._dispatch_budget: int | None = None
        self._pause = asyncio.Event()
        self._pause.set()
        self._cancelled = False

    def approve(self, revision_hash: str) -> None:
        if revision_hash != self.revision.revision_hash:
            raise ValueError("approval hash does not match graph revision")
        self.approved_hash = revision_hash
        self._emit("graph_approved", {"revision_hash": revision_hash})

    def pause(self) -> None:
        self._pause.clear()
        self._emit("orchestration_paused", {})

    def resume(self) -> None:
        self._pause.set()
        self._emit("orchestration_resumed", {})

    def cancel(self) -> None:
        self._cancelled = True
        self._pause.set()
        self._emit("orchestration_cancel_requested", {})

    def add_breakpoint(self, node_id: str) -> None:
        if node_id not in self.states:
            raise KeyError(node_id)
        self.breakpoints.add(node_id)
        self._emit("breakpoint_set", {"node_id": node_id})

    def remove_breakpoint(self, node_id: str) -> None:
        self.breakpoints.discard(node_id)
        self._emit("breakpoint_cleared", {"node_id": node_id})

    def step(self) -> None:
        """Allow exactly one additional node dispatch, then pause."""
        self._dispatch_budget = 1
        self._pause.set()
        self._emit("orchestration_step_requested", {})

    async def run(self, runner: NodeRunner) -> dict[str, NodeState]:
        if self.approved_hash != self.revision.revision_hash:
            raise PermissionError("the current graph revision is not approved")
        nodes = {node.node_id: node for node in self.revision.graph.nodes}
        order = self.revision.graph.topological_order()
        dispatch_index = 0
        running: dict[str, asyncio.Task[NodeResult]] = {}
        workspaces: dict[str, Path] = {}
        self.state_root.mkdir(parents=True, exist_ok=True)
        snapshot = self.state_root / "snapshot"
        if snapshot.exists():
            shutil.rmtree(snapshot)
        shutil.copytree(self.repository, snapshot, ignore=shutil.ignore_patterns(".git"), symlinks=False)
        try:
            while True:
                await self._pause.wait()
                if self._cancelled:
                    for task in running.values():
                        task.cancel()
                    for node_id, state in self.states.items():
                        if state in {NodeState.PENDING, NodeState.RUNNING}:
                            self.states[node_id] = NodeState.CANCELLED
                    break
                for node_id in order:
                    if len(running) >= self.concurrency:
                        break
                    node = nodes[node_id]
                    if self.states[node_id] is not NodeState.PENDING:
                        continue
                    if node_id in self.breakpoints and self._dispatch_budget is None:
                        self._pause.clear()
                        self._emit("breakpoint_hit", {"node_id": node_id}, node_id=node_id)
                        break
                    if self._dispatch_budget == 0:
                        self._pause.clear()
                        break
                    dependency_states = [self.states[item] for item in node.dependencies]
                    if any(state in {NodeState.FAILED, NodeState.BLOCKED, NodeState.CANCELLED} for state in dependency_states):
                        self.states[node_id] = NodeState.BLOCKED
                        self._emit("node_blocked", {}, node_id=node_id)
                        continue
                    if not all(state is NodeState.SUCCEEDED for state in dependency_states):
                        continue
                    provider = node.provider_policy.provider_at(dispatch_index)
                    dispatch_index += 1
                    # Every agent receives its own copy. Read-only roles also
                    # get filesystem-enforced immutable files, so a faulty tool
                    # cannot contaminate another node's snapshot.
                    workspace = Path(tempfile.mkdtemp(prefix=f"node-{node_id}-", dir=self.state_root))
                    shutil.copytree(snapshot, workspace, dirs_exist_ok=True, symlinks=False)
                    if not node.edits:
                        _make_read_only(workspace)
                    workspaces[node_id] = workspace
                    self.providers[node_id] = provider
                    self.states[node_id] = NodeState.RUNNING
                    self._emit("node_dispatched", {"provider": provider, "dispatch_index": dispatch_index - 1}, node_id=node_id)
                    running[node_id] = asyncio.create_task(runner(node, provider, workspace, snapshot))
                    if self._dispatch_budget is not None:
                        self._dispatch_budget -= 1
                if not running:
                    if all(state not in {NodeState.PENDING, NodeState.RUNNING} for state in self.states.values()):
                        break
                    await asyncio.sleep(0)
                    continue
                done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
                for node_id in [item for item in order if running.get(item) in done]:
                    task = running.pop(node_id)
                    try:
                        result = task.result()
                    except Exception as exc:
                        result = NodeResult(False, message=f"{type(exc).__name__}: {exc}")
                    self.states[node_id] = NodeState.SUCCEEDED if result.success else NodeState.FAILED
                    if result.success and nodes[node_id].edits:
                        self.merge_queue.append(node_id)
                        proposal = self.state_root / "proposals" / node_id
                        if proposal.exists():
                            shutil.rmtree(proposal)
                        proposal.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(workspaces[node_id], proposal, symlinks=False)
                        self.proposals[node_id] = proposal
                    self._emit("node_succeeded" if result.success else "node_failed",
                               {"message": result.message, "artifacts": list(result.artifacts)}, node_id=node_id)
        finally:
            for node_id, workspace in workspaces.items():
                if workspace.exists():
                    if not nodes[node_id].edits:
                        _make_writable(workspace)
                    shutil.rmtree(workspace)
        return dict(self.states)

    def _emit(self, event_type: str, payload: dict, *, node_id: str | None = None) -> None:
        if self.journal:
            self.journal.append(event_type, payload, graph_revision=self.revision.revision, node_id=node_id)


def hash_paths(repository: Path, paths: tuple[str, ...]) -> dict[str, str]:
    return {path: (hashlib.sha256((repository / path).read_bytes()).hexdigest()
                   if (repository / path).is_file() else "missing") for path in paths}


def _make_read_only(root: Path) -> None:
    for path in root.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _make_writable(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
