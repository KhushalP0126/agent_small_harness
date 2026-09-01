from pathlib import Path

import pytest

from harness_kernel.orchestration_store import OrchestrationStore
from harness_kernel.task_graph import ProviderPolicy, TaskGraph, TaskNode


def graph() -> TaskGraph:
    policy = ProviderPolicy(("qwen", "api"), (1, 1))
    return TaskGraph((TaskNode("a", "implementer", "python", write_paths=("a.py",), provider_policy=policy),
                      TaskNode("b", "validator", "python", dependencies=("a",), provider_policy=policy)))


def test_store_resumes_state_and_retries_descendants(tmp_path: Path) -> None:
    first = OrchestrationStore(tmp_path)
    state = first.create(graph(), goal="demo")
    session_id = state["session_id"]
    first.approve(session_id, state["revision"]["revision_hash"])
    first.record_attempt(session_id, "a", "failed", "qwen", message="provider failed")
    state = first.get(session_id)
    state["node_states"]["b"] = "blocked"
    first._write(session_id, state)
    resumed = OrchestrationStore(tmp_path)
    state = resumed.retry(session_id, "a", provider="api")
    assert state["node_states"] == {"a": "pending", "b": "pending"}
    assert state["provider_overrides"]["a"] == "api"
    assert state["attempts"]["a"][0]["message"] == "provider failed"


def test_retry_provider_must_be_in_approved_policy(tmp_path: Path) -> None:
    store = OrchestrationStore(tmp_path)
    state = store.create(graph())
    store.record_attempt(state["session_id"], "a", "failed", "qwen")
    with pytest.raises(ValueError, match="not allowed"):
        store.retry(state["session_id"], "a", "invented")


def test_revision_invalidates_approval_and_preserves_known_attempts(tmp_path: Path) -> None:
    store = OrchestrationStore(tmp_path)
    state = store.create(graph())
    sid = state["session_id"]
    store.approve(sid, state["revision"]["revision_hash"])
    store.record_attempt(sid, "a", "succeeded", "qwen")
    revised = store.revise(sid, graph(), "new work")
    assert revised["approved_hash"] is None
    assert revised["status"] == "awaiting_approval"
    assert len(revised["attempts"]["a"]) == 1


def test_breakpoint_and_step_are_persisted(tmp_path: Path) -> None:
    store = OrchestrationStore(tmp_path); state = store.create(graph()); sid = state["session_id"]
    state = store.debug_control(sid, "break", "a")
    assert state["breakpoints"] == ["a"]
    state = store.debug_control(sid, "step")
    assert state["step_requested"] is True
