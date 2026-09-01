from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from harness_kernel.event_journal import EventJournal
from harness_kernel.checkpoints import CheckpointStore
from harness_kernel.governance import PermissionEvaluator
from harness_kernel.merge_queue import SerializedMergeQueue
from harness_kernel.orchestration import NodeResult, NodeState, OrchestrationSession
from harness_kernel.roles import RoleRegistry
from harness_kernel.task_graph import GraphRevision, ProviderPolicy, TaskGraph, TaskNode


def node(node_id: str, *, dependencies=(), writes=(), policy=None) -> TaskNode:
    return TaskNode(node_id, "implementer" if writes else "validator", "python",
                    tuple(dependencies), write_paths=tuple(writes),
                    provider_policy=policy or ProviderPolicy())


def test_graph_rejects_cycles_conflicts_and_unsafe_paths() -> None:
    with pytest.raises(ValueError, match="cycle"):
        TaskGraph((node("a", dependencies=("b",)), node("b", dependencies=("a",))))
    with pytest.raises(ValueError, match="write conflict"):
        TaskGraph((node("a", writes=("src",)), node("b", writes=("src/a.py",))))
    with pytest.raises(ValueError, match="unsafe"):
        node("a", writes=("../escape",))


def test_ordered_write_claims_and_revision_hash() -> None:
    graph = TaskGraph((node("b", dependencies=("a",), writes=("src/a.py",)),
                       node("a", writes=("src/a.py",))))
    assert graph.topological_order() == ("a", "b")
    revision = GraphRevision(1, graph)
    assert revision.revise(graph, "retry").parent_hash == revision.revision_hash


def test_role_capabilities_are_closed() -> None:
    with pytest.raises(PermissionError):
        RoleRegistry().authorize("validator", "write", language="python", provider="qwen")
    with pytest.raises(ValueError, match="unregistered"):
        RoleRegistry().get("invented")


def test_three_nodes_concurrent_and_routing_independent_of_completion(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "kept.txt").write_text("original")
    policy = ProviderPolicy(("qwen", "api"), (2, 1))
    graph = TaskGraph(tuple(node(name, writes=(f"{name}.txt",), policy=policy) for name in ("a", "b", "c")))
    revision = GraphRevision(1, graph)
    active = 0
    peak = 0

    async def runner(task, provider, workspace, snapshot):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        (workspace / task.write_paths[0]).write_text(provider)
        await asyncio.sleep({"a": .03, "b": .02, "c": .01}[task.node_id])
        active -= 1
        return NodeResult(True)

    session = OrchestrationSession(revision, repository, tmp_path / "state")
    session.approve(revision.revision_hash)
    states = asyncio.run(session.run(runner))
    assert peak == 3
    assert all(state is NodeState.SUCCEEDED for state in states.values())
    assert session.providers == {"a": "qwen", "b": "qwen", "c": "api"}
    assert (repository / "kept.txt").read_text() == "original"
    assert not any((repository / f"{name}.txt").exists() for name in "abc")


def test_journal_redacts_and_replay_is_read_only(tmp_path: Path) -> None:
    journal = EventJournal(tmp_path, "session")
    ref = journal.put_blob(b"artifact")
    journal.append("tool_call", {"api_key": "secret", "header": "Bearer abc", "artifact": ref}, graph_revision=1)
    events = list(journal.replay())
    assert events[0].payload["api_key"] == "[REDACTED]"
    assert events[0].payload["header"] == "Bearer [REDACTED]"
    assert journal.get_blob(ref) == b"artifact"


def test_reviewed_merge_rejects_stale_then_applies_valid_proposal(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    repository.mkdir(); workspace.mkdir()
    (repository / "a.py").write_text("old")
    (workspace / "a.py").write_text("new")
    queue = SerializedMergeQueue(repository, CheckpointStore(repository, tmp_path / "checkpoints"),
                                 PermissionEvaluator())
    editing_node = node("edit", writes=("a.py",))
    queue.enqueue(editing_node, workspace, {"a.py": "stale"})
    with pytest.raises(RuntimeError, match="stale"):
        queue.review_next(approved=True, session_id="s", validate=lambda *_: True)
    import hashlib
    current = hashlib.sha256(b"old").hexdigest()
    queue.enqueue(editing_node, workspace, {"a.py": current})
    checkpoint = queue.review_next(approved=True, session_id="s", validate=lambda *_: True)
    assert checkpoint is not None
    assert (repository / "a.py").read_text() == "new"
