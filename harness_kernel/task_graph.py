"""Immutable, approval-gated task graphs used by supervised orchestration."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Iterable

from harness_kernel.task_ir import TaskIR


class Role(str, Enum):
    PLANNER = "planner"
    RESEARCHER = "researcher"
    IMPLEMENTER = "implementer"
    VALIDATOR = "validator"
    CONFLICT_REPAIR = "conflict-repair"


LANGUAGES = frozenset({"python", "c", "cpp", "rust", "javascript"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def safe_repo_path(value: str) -> str:
    """Return a canonical repository-relative path or reject it."""
    if not value or "\\" in value or "\x00" in value:
        raise ValueError(f"unsafe repository path: {value!r}")
    path = PurePosixPath(value)
    normalized = posixpath.normpath(value)
    if path.is_absolute() or normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise ValueError(f"unsafe repository path: {value!r}")
    if normalized != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"repository path must be canonical: {value!r}")
    return normalized


@dataclass(frozen=True)
class ProviderPolicy:
    providers: tuple[str, ...] = ("qwen",)
    weights: tuple[int, ...] = (1,)

    def __post_init__(self) -> None:
        if not self.providers or len(self.providers) != len(self.weights):
            raise ValueError("provider policy needs one positive weight per provider")
        if any(not item.strip() for item in self.providers) or any(weight <= 0 for weight in self.weights):
            raise ValueError("provider names and weights must be non-empty and positive")

    def provider_at(self, dispatch_index: int) -> str:
        position = dispatch_index % sum(self.weights)
        for provider, weight in zip(self.providers, self.weights, strict=True):
            if position < weight:
                return provider
            position -= weight
        raise AssertionError("unreachable provider schedule")


@dataclass(frozen=True)
class TaskNode:
    node_id: str
    role: str
    language: str
    dependencies: tuple[str, ...] = ()
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    provider_policy: ProviderPolicy = field(default_factory=ProviderPolicy)
    validation_profile: str = "default"
    trusted_tests: tuple[str, ...] = ()
    inputs: dict[str, Any] = field(default_factory=dict)
    artifact_outputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.node_id):
            raise ValueError(f"invalid node ID: {self.node_id!r}")
        try:
            Role(self.role)
        except ValueError as exc:
            raise ValueError(f"unknown role: {self.role}") from exc
        if self.language not in LANGUAGES:
            raise ValueError(f"unknown language: {self.language}")
        for collection in (self.read_paths, self.write_paths, self.trusted_tests, self.artifact_outputs):
            for path in collection:
                safe_repo_path(path)
        if self.role in {Role.PLANNER.value, Role.RESEARCHER.value, Role.VALIDATOR.value} and self.write_paths:
            raise ValueError(f"role {self.role} is read-only")

    @property
    def edits(self) -> bool:
        return bool(self.write_paths)


@dataclass(frozen=True)
class TaskGraph:
    nodes: tuple[TaskNode, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError(f"unsupported task graph schema: {self.schema_version}")
        by_id = {node.node_id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise ValueError("task graph node IDs must be unique")
        for node in self.nodes:
            unknown = set(node.dependencies) - by_id.keys()
            if unknown:
                raise ValueError(f"node {node.node_id} has unknown dependencies: {sorted(unknown)}")
            if node.node_id in node.dependencies:
                raise ValueError(f"node {node.node_id} depends on itself")
        self.topological_order()  # cycle check
        editing = [node for node in self.nodes if node.edits]
        for index, left in enumerate(editing):
            for right in editing[index + 1 :]:
                if _claims_overlap(left.write_paths, right.write_paths):
                    if not (self.depends_on(left.node_id, right.node_id) or self.depends_on(right.node_id, left.node_id)):
                        raise ValueError(
                            f"write conflict between {left.node_id} and {right.node_id} requires an ordering edge"
                        )

    def topological_order(self) -> tuple[str, ...]:
        dependencies = {node.node_id: set(node.dependencies) for node in self.nodes}
        ordered: list[str] = []
        while dependencies:
            ready = sorted(node_id for node_id, deps in dependencies.items() if not deps)
            if not ready:
                raise ValueError("task graph contains a cycle")
            ordered.extend(ready)
            for node_id in ready:
                dependencies.pop(node_id)
            for deps in dependencies.values():
                deps.difference_update(ready)
        return tuple(ordered)

    def depends_on(self, node_id: str, ancestor_id: str) -> bool:
        by_id = {node.node_id: node for node in self.nodes}
        pending = list(by_id[node_id].dependencies)
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == ancestor_id:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(by_id[current].dependencies)
        return False

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def graph_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


@dataclass(frozen=True)
class GraphRevision:
    revision: int
    graph: TaskGraph
    parent_hash: str | None = None
    reason: str = "initial proposal"

    def __post_init__(self) -> None:
        if self.revision < 1 or (self.revision == 1) != (self.parent_hash is None):
            raise ValueError("initial revision has no parent; later revisions require one")

    @property
    def revision_hash(self) -> str:
        payload = {"revision": self.revision, "parent_hash": self.parent_hash, "reason": self.reason,
                   "graph_hash": self.graph.graph_hash}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def revise(self, graph: TaskGraph, reason: str) -> "GraphRevision":
        return GraphRevision(self.revision + 1, graph, self.revision_hash, reason)

    def diff(self, previous: "GraphRevision") -> dict[str, Any]:
        old = {node.node_id: asdict(node) for node in previous.graph.nodes}
        new = {node.node_id: asdict(node) for node in self.graph.nodes}
        return {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
                "changed": sorted(key for key in new.keys() & old.keys() if new[key] != old[key])}


def compile_task_ir(task: TaskIR) -> GraphRevision:
    """Compatibility adapter: legacy work is an approved one-node candidate graph."""
    from harness_kernel.language_adapters import get_language_profile
    files = tuple(safe_repo_path(path) for path in task.files)
    language = get_language_profile(task.language).language
    node = TaskNode(node_id="legacy-task", role=Role.IMPLEMENTER.value, language=language,
                    read_paths=files, write_paths=files, capabilities=("read", "write", "command"),
                    inputs={"task_ir": asdict(task)})
    return GraphRevision(1, TaskGraph((node,)))


def _claims_overlap(left: Iterable[str], right: Iterable[str]) -> bool:
    def overlaps(a: str, b: str) -> bool:
        return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")
    return any(overlaps(a, b) for a in left for b in right)
