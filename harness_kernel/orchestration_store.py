"""Persistent graph/session lifecycle used by API and TUI frontends."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from harness_kernel.event_journal import EventJournal
from harness_kernel.task_graph import GraphRevision, ProviderPolicy, TaskGraph, TaskNode


class OrchestrationStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def create(self, graph: TaskGraph, *, goal: str = "") -> dict[str, Any]:
        session_id = f"orch-{uuid.uuid4().hex}"
        revision = GraphRevision(1, graph)
        state = {"session_id": session_id, "goal": goal, "status": "awaiting_approval",
                 "approved_hash": None, "revision": _revision_dict(revision),
                 "node_states": {node.node_id: "pending" for node in graph.nodes},
                 "attempts": {node.node_id: [] for node in graph.nodes},
                 "provider_overrides": {}, "breakpoints": [], "step_requested": False}
        self._write(session_id, state)
        self.journal(session_id).append("graph_proposed", {"revision_hash": revision.revision_hash,
                                        "graph_hash": graph.graph_hash}, graph_revision=1)
        return state

    def get(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.is_file():
            raise KeyError(session_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def approve(self, session_id: str, revision_hash: str) -> dict[str, Any]:
        state = self.get(session_id)
        if revision_hash != state["revision"]["revision_hash"]:
            raise ValueError("approval hash does not match current revision")
        state.update(status="approved", approved_hash=revision_hash)
        self._write(session_id, state)
        self.journal(session_id).append("graph_approved", {"revision_hash": revision_hash},
                                        graph_revision=state["revision"]["revision"])
        return state

    def transition(self, session_id: str, status: str) -> dict[str, Any]:
        allowed = {"paused", "running", "cancelled"}
        if status not in allowed:
            raise ValueError("invalid orchestration transition")
        state = self.get(session_id)
        if status == "running" and state["approved_hash"] != state["revision"]["revision_hash"]:
            raise PermissionError("current graph revision is not approved")
        if state["status"] == "cancelled":
            raise ValueError("cancelled sessions are terminal")
        state["status"] = status
        self._write(session_id, state)
        self.journal(session_id).append(f"orchestration_{status}", {},
                                        graph_revision=state["revision"]["revision"])
        return state

    def revise(self, session_id: str, graph: TaskGraph, reason: str) -> dict[str, Any]:
        state = self.get(session_id)
        old = revision_from_dict(state["revision"])
        revision = old.revise(graph, reason)
        previous_states = state.get("node_states", {})
        previous_attempts = state.get("attempts", {})
        state.update(status="awaiting_approval", approved_hash=None, revision=_revision_dict(revision),
                     node_states={node.node_id: previous_states.get(node.node_id, "pending") for node in graph.nodes},
                     attempts={node.node_id: previous_attempts.get(node.node_id, []) for node in graph.nodes})
        self._write(session_id, state)
        self.journal(session_id).append("graph_revised", {"revision_hash": revision.revision_hash,
                                        "diff": revision.diff(old)}, graph_revision=revision.revision)
        return state

    def record_attempt(self, session_id: str, node_id: str, status: str, provider: str,
                       *, message: str = "") -> dict[str, Any]:
        if status not in {"running", "succeeded", "failed", "cancelled"}:
            raise ValueError("invalid node attempt status")
        state = self.get(session_id)
        if node_id not in state["node_states"]:
            raise KeyError(node_id)
        attempts = state.setdefault("attempts", {}).setdefault(node_id, [])
        attempt_id = f"{node_id}-attempt-{len(attempts) + 1}"
        attempts.append({"attempt_id": attempt_id, "status": status, "provider": provider,
                         "message": message})
        state["node_states"][node_id] = status
        self._write(session_id, state)
        self.journal(session_id).append("node_attempt_recorded", attempts[-1],
                                        graph_revision=state["revision"]["revision"], node_id=node_id,
                                        attempt_id=attempt_id)
        return state

    def retry(self, session_id: str, node_id: str, provider: str | None = None) -> dict[str, Any]:
        state = self.get(session_id)
        if node_id not in state["node_states"]:
            raise KeyError(node_id)
        if state["node_states"][node_id] not in {"failed", "blocked", "cancelled"}:
            raise ValueError("only failed, blocked, or cancelled nodes may be retried")
        if provider:
            allowed = _node_providers(state, node_id)
            if provider not in allowed:
                raise ValueError(f"provider {provider!r} is not allowed by the approved node policy")
            state.setdefault("provider_overrides", {})[node_id] = provider
        state["node_states"][node_id] = "pending"
        # Descendants blocked solely by this failure become schedulable again.
        graph = graph_from_dict(state["revision"]["graph"])
        for candidate in graph.nodes:
            if graph.depends_on(candidate.node_id, node_id) and state["node_states"].get(candidate.node_id) == "blocked":
                state["node_states"][candidate.node_id] = "pending"
        self._write(session_id, state)
        self.journal(session_id).append("node_retry_requested", {"provider": provider or "policy"},
                                        graph_revision=state["revision"]["revision"], node_id=node_id)
        return state

    def debug_control(self, session_id: str, action: str, node_id: str = "") -> dict[str, Any]:
        state = self.get(session_id)
        if action == "break":
            if node_id not in state["node_states"]:
                raise KeyError(node_id)
            values = set(state.get("breakpoints", [])); values.add(node_id)
            state["breakpoints"] = sorted(values)
        elif action == "clear_break":
            state["breakpoints"] = [item for item in state.get("breakpoints", []) if item != node_id]
        elif action == "step":
            state["step_requested"] = True
            state["status"] = "running"
        else:
            raise ValueError("unknown debug action")
        self._write(session_id, state)
        self.journal(session_id).append(f"orchestration_{action}", {"node_id": node_id},
                                        graph_revision=state["revision"]["revision"])
        return state

    def journal(self, session_id: str) -> EventJournal:
        return EventJournal(self.root / session_id, session_id)

    def _path(self, session_id: str) -> Path:
        if not session_id.startswith("orch-") or not session_id[5:].isalnum():
            raise KeyError(session_id)
        return self.root / session_id / "session.json"

    def _write(self, session_id: str, value: dict[str, Any]) -> None:
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(path)


def graph_from_dict(value: dict[str, Any]) -> TaskGraph:
    nodes = []
    for raw in value.get("nodes", []):
        item = dict(raw)
        policy = item.get("provider_policy", {})
        item["provider_policy"] = ProviderPolicy(tuple(policy.get("providers", ("qwen",))),
                                                   tuple(policy.get("weights", (1,))))
        for key in ("dependencies", "read_paths", "write_paths", "capabilities", "trusted_tests", "artifact_outputs"):
            item[key] = tuple(item.get(key, ()))
        nodes.append(TaskNode(**item))
    return TaskGraph(tuple(nodes), value.get("schema_version", 1))


def revision_from_dict(value: dict[str, Any]) -> GraphRevision:
    return GraphRevision(value["revision"], graph_from_dict(value["graph"]), value.get("parent_hash"), value["reason"])


def _revision_dict(revision: GraphRevision) -> dict[str, Any]:
    value = asdict(revision)
    value["revision_hash"] = revision.revision_hash
    value["graph_hash"] = revision.graph.graph_hash
    return value


def _node_providers(state: dict[str, Any], node_id: str) -> tuple[str, ...]:
    graph = graph_from_dict(state["revision"]["graph"])
    return next(node.provider_policy.providers for node in graph.nodes if node.node_id == node_id)
