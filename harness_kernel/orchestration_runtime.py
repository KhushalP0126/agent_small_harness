"""End-to-end runtime connecting approved graphs, typed agents and validation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from harness_kernel.event_journal import EventJournal
from harness_kernel.orchestration import NodeResult, NodeState, OrchestrationSession, hash_paths
from harness_kernel.orchestration_store import OrchestrationStore, revision_from_dict
from harness_kernel.checkpoints import CheckpointStore
from harness_kernel.governance import PermissionEvaluator
from harness_kernel.merge_queue import SerializedMergeQueue, TrustedValidator
from harness_kernel.project_validation import validate_project
from harness_kernel.roles import RoleRegistry
from harness_kernel.task_graph import Role, TaskNode


AgentHandler = Callable[[TaskNode, str, Path, Path], Awaitable[NodeResult]]


@dataclass
class TypedNodeExecutor:
    """Dispatch roles through registered handlers without implicit fallback."""
    handlers: dict[str, AgentHandler]
    roles: RoleRegistry = field(default_factory=RoleRegistry)
    validation_mode: str = "container"

    async def __call__(self, node: TaskNode, provider: str, workspace: Path,
                       snapshot: Path) -> NodeResult:
        manifest = self.roles.get(node.role)
        if provider not in manifest.providers or node.language not in manifest.languages:
            return NodeResult(False, message=f"role policy denies {node.language}/{provider}")
        for capability in node.capabilities:
            self.roles.authorize(node.role, capability, language=node.language, provider=provider)
        handler = self.handlers.get(node.role)
        if handler is not None:
            return await handler(node, provider, workspace, snapshot)
        if node.role == Role.VALIDATOR.value:
            result = await asyncio.to_thread(validate_project, workspace, node.language,
                                             mode=self.validation_mode)
            return NodeResult(result.passed, message=f"validation tier={result.tier}")
        return NodeResult(False, message=f"no executor registered for role {node.role}; no fallback performed")


class PersistedOrchestrationRuntime:
    def __init__(self, store: OrchestrationStore, session_id: str, repository: Path,
                 executor: TypedNodeExecutor, *, concurrency: int = 3) -> None:
        self.store = store
        self.session_id = session_id
        self.repository = repository.resolve()
        self.executor = executor
        state = store.get(session_id)
        revision = revision_from_dict(state["revision"])
        self.session = OrchestrationSession(
            revision, self.repository, store.root / session_id / "runtime",
            concurrency=concurrency, journal=store.journal(session_id),
        )
        self.session.approved_hash = state.get("approved_hash")
        self.session.breakpoints.update(state.get("breakpoints", []))
        if state.get("step_requested"):
            self.session.step()
        for node_id, raw in state.get("node_states", {}).items():
            if node_id in self.session.states and raw in NodeState._value2member_map_:
                # A process that died while running is safely retried as pending.
                self.session.states[node_id] = NodeState.PENDING if raw == "running" else NodeState(raw)
        self.original_hashes = {
            node.node_id: hash_paths(self.repository, node.write_paths)
            for node in revision.graph.nodes if node.edits
        }

    async def run(self) -> dict[str, NodeState]:
        states = await self.session.run(self.executor)
        persisted = self.store.get(self.session_id)
        persisted["node_states"] = {node_id: state.value for node_id, state in states.items()}
        persisted["status"] = "completed" if all(state is NodeState.SUCCEEDED for state in states.values()) else "failed"
        persisted["merge_queue"] = list(self.session.merge_queue)
        persisted["proposals"] = {node_id: str(path) for node_id, path in self.session.proposals.items()}
        persisted["original_hashes"] = self.original_hashes
        for node_id, state in states.items():
            if state in {NodeState.SUCCEEDED, NodeState.FAILED, NodeState.CANCELLED}:
                provider = self.session.providers.get(node_id, persisted.get("provider_overrides", {}).get(node_id, "unassigned"))
                self.store.record_attempt(self.session_id, node_id, state.value, provider)
        # record_attempt reloads/writes state; merge metadata must be applied last.
        latest = self.store.get(self.session_id)
        latest.update({key: persisted[key] for key in ("node_states", "status", "merge_queue", "proposals", "original_hashes")})
        self.store._write(self.session_id, latest)
        return states

    def review_merge(self, node_id: str, *, approved: bool,
                     validate: TrustedValidator) -> str | None:
        """Apply one retained proposal through stale-hash and trusted-test gates."""
        state = self.store.get(self.session_id)
        if not state.get("merge_queue") or state["merge_queue"][0] != node_id:
            raise ValueError("merge review must follow serialized queue order")
        revision = revision_from_dict(state["revision"])
        node = next(item for item in revision.graph.nodes if item.node_id == node_id)
        queue = SerializedMergeQueue(
            self.repository,
            CheckpointStore(self.repository, self.store.root / self.session_id / "merge_checkpoints"),
            PermissionEvaluator(),
        )
        queue.enqueue(node, Path(state["proposals"][node_id]), state["original_hashes"][node_id])
        checkpoint = queue.review_next(approved=approved, session_id=self.session_id, validate=validate)
        state["merge_queue"].pop(0)
        state.setdefault("merge_outcomes", {})[node_id] = {
            "approved": approved, "checkpoint_id": checkpoint.checkpoint_id if checkpoint else None
        }
        self.store._write(self.session_id, state)
        self.store.journal(self.session_id).append(
            "merge_applied" if checkpoint else "merge_rejected",
            state["merge_outcomes"][node_id], graph_revision=revision.revision, node_id=node_id,
        )
        return checkpoint.checkpoint_id if checkpoint else None
