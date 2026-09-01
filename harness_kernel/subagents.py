"""Isolated subagent workspaces and reviewed merge proposals."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from harness_kernel.tool_paths import resolve_within_root


@dataclass(frozen=True)
class SubagentDiff:
    changed_paths: tuple[str, ...]
    contents: dict[str, str | None]
    original_hashes: dict[str, str]
    validation_evidence: tuple[str, ...]


class IsolatedSubagentWorkspace:
    """Copy a repository into a temporary edit root; never expose shared writes."""

    def __init__(self, repository: Path) -> None:
        self.repository = repository.resolve()
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None
        self._baseline: dict[str, str] = {}

    def __enter__(self) -> "IsolatedSubagentWorkspace":
        self._temporary = tempfile.TemporaryDirectory(prefix="harness-subagent-")
        self.path = Path(self._temporary.name) / "workspace"
        shutil.copytree(
            self.repository, self.path,
            ignore=shutil.ignore_patterns(".git", "artifacts", "target", "__pycache__", ".pytest_cache"),
        )
        self._baseline = self._hashes(self.path)
        return self

    def __exit__(self, *_args: object) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
        self.path = None

    def diff(self, validation_evidence: list[str]) -> SubagentDiff:
        if self.path is None:
            raise RuntimeError("subagent workspace is not active")
        current = self._hashes(self.path)
        changed = tuple(sorted(path for path in self._baseline.keys() | current.keys() if self._baseline.get(path) != current.get(path)))
        contents = {
            relative: ((self.path / relative).read_text(encoding="utf-8") if (self.path / relative).is_file() else None)
            for relative in changed
        }
        return SubagentDiff(changed, contents, {path: self._baseline.get(path, "missing") for path in changed}, tuple(validation_evidence))

    def merge_reviewed(self, proposal: SubagentDiff, *, approved: bool) -> bool:
        if not approved:
            return False
        for relative in proposal.changed_paths:
            destination = resolve_within_root(self.repository, relative)
            current = self._hash_file(destination)
            if current != proposal.original_hashes[relative]:
                raise ValueError(f"shared workspace changed after subagent snapshot: {relative}")
        for relative in proposal.changed_paths:
            destination = resolve_within_root(self.repository, relative)
            content = proposal.contents[relative]
            if content is None:
                destination.unlink(missing_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8")
        return True

    @classmethod
    def _hashes(cls, root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): cls._hash_file(path)
            for path in root.rglob("*") if path.is_file()
        }

    @staticmethod
    def _hash_file(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
