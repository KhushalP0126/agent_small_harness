import asyncio
from pathlib import Path

from harness_kernel.orchestration import NodeResult, NodeState
from harness_kernel.orchestration_runtime import PersistedOrchestrationRuntime, TypedNodeExecutor
from harness_kernel.orchestration_store import OrchestrationStore
from harness_kernel.task_graph import TaskGraph, TaskNode


def test_runtime_persists_attempts_and_edit_proposal_without_shared_write(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / "a.py").write_text("old")
    graph = TaskGraph((TaskNode("edit", "implementer", "python", write_paths=("a.py",),
                                capabilities=("read", "write")),))
    store = OrchestrationStore(tmp_path / "state")
    state = store.create(graph); store.approve(state["session_id"], state["revision"]["revision_hash"])
    async def implement(node, provider, workspace, snapshot):
        (workspace / "a.py").write_text("new")
        return NodeResult(True)
    runtime = PersistedOrchestrationRuntime(store, state["session_id"], repo,
                                             TypedNodeExecutor({"implementer": implement}, validation_mode="local"))
    states = asyncio.run(runtime.run())
    assert states["edit"] is NodeState.SUCCEEDED
    assert (repo / "a.py").read_text() == "old"
    persisted = store.get(state["session_id"])
    assert Path(persisted["proposals"]["edit"]).joinpath("a.py").read_text() == "new"
    assert persisted["attempts"]["edit"][0]["status"] == "succeeded"
    checkpoint = runtime.review_merge("edit", approved=True, validate=lambda workspace, refs: True)
    assert checkpoint.startswith("cp-")
    assert (repo / "a.py").read_text() == "new"


def test_missing_role_handler_fails_without_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    graph = TaskGraph((TaskNode("plan", "planner", "python"),))
    store = OrchestrationStore(tmp_path / "state"); state = store.create(graph)
    store.approve(state["session_id"], state["revision"]["revision_hash"])
    runtime = PersistedOrchestrationRuntime(store, state["session_id"], repo, TypedNodeExecutor({}))
    states = asyncio.run(runtime.run())
    assert states["plan"] is NodeState.FAILED
