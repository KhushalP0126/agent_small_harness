"""Content checkpoints and transactional rewind without Git history changes."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    parent_id: str | None
    session_id: str
    timestamp: float
    changed_paths: tuple[str, ...]
    content_hashes: dict[str, str]
    conversation_summary: str
    approval_state: str


class CheckpointStore:
    _CHECKPOINT_ID = re.compile(r"cp-[0-9a-f]{16}\Z")
    def __init__(self, repository: Path, state_root: Path) -> None:
        self.repository = repository.resolve()
        self.state_root = state_root.resolve()

    def create(self, session_id: str, paths: list[str], *, parent_id: str | None = None, conversation_summary: str = "", approval_state: str = "approved") -> Checkpoint:
        checkpoint_id = f"cp-{uuid.uuid4().hex[:16]}"
        self.state_root.mkdir(parents=True, exist_ok=True)
        root = self.state_root / checkpoint_id
        content = root / "content"
        content.mkdir(parents=True)
        hashes: dict[str, str] = {}
        safe_paths = tuple(sorted({self._relative_path(path) for path in paths}))
        for relative in safe_paths:
            source = self._safe_path(relative)
            if source.is_file():
                target = content / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                hashes[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
            elif not source.exists():
                hashes[relative] = "missing"
            else:
                raise ValueError(f"checkpoint path is not a regular file: {relative}")
        checkpoint = Checkpoint(checkpoint_id, parent_id, session_id, time.time(), safe_paths, hashes, conversation_summary, approval_state)
        (root / "metadata.json").write_text(json.dumps(asdict(checkpoint), indent=2), encoding="utf-8")
        return checkpoint

    def list(self, session_id: str | None = None) -> list[Checkpoint]:
        values = []
        for path in self.state_root.glob("cp-*/metadata.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                value["changed_paths"] = tuple(value["changed_paths"])
                checkpoint = Checkpoint(**value)
                if session_id is None or checkpoint.session_id == session_id:
                    values.append(checkpoint)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(values, key=lambda item: item.timestamp, reverse=True)

    def restore(self, checkpoint_id: str) -> Checkpoint:
        root = self._checkpoint_root(checkpoint_id)
        metadata = root / "metadata.json"
        value = json.loads(metadata.read_text(encoding="utf-8"))
        value["changed_paths"] = tuple(value["changed_paths"])
        checkpoint = Checkpoint(**value)
        if checkpoint.checkpoint_id != checkpoint_id:
            raise ValueError("checkpoint metadata ID does not match its directory")
        normalized = tuple(self._relative_path(path) for path in checkpoint.changed_paths)
        if normalized != checkpoint.changed_paths:
            raise ValueError("checkpoint metadata contains a non-canonical path")
        for relative in checkpoint.changed_paths:
            destination = self._safe_path(relative)
            snapshot = root / "content" / relative
            expected = checkpoint.content_hashes.get(relative)
            if expected is None:
                raise ValueError(f"checkpoint is missing a hash for {relative}")
            if expected == "missing":
                if snapshot.exists():
                    raise ValueError(f"unexpected snapshot content for {relative}")
            elif not snapshot.is_file() or hashlib.sha256(snapshot.read_bytes()).hexdigest() != expected:
                raise ValueError(f"checkpoint content hash mismatch for {relative}")
            if destination.exists() and not destination.is_file():
                raise ValueError(f"rewind destination is not a regular file: {relative}")
        # Capture every destination before touching the workspace. If any copy
        # fails, roll back from this private temporary directory.
        with tempfile.TemporaryDirectory(prefix="rewind-") as temporary:
            rollback = Path(temporary)
            existed: set[str] = set()
            for relative in checkpoint.changed_paths:
                destination = self._safe_path(relative)
                if destination.is_file():
                    existed.add(relative)
                    backup = rollback / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, backup)
            try:
                for relative in checkpoint.changed_paths:
                    destination = self._safe_path(relative)
                    snapshot = root / "content" / relative
                    if snapshot.is_file():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(snapshot, destination)
                    elif destination.exists():
                        destination.unlink()
            except OSError:
                for relative in checkpoint.changed_paths:
                    destination = self._safe_path(relative)
                    backup = rollback / relative
                    if relative in existed:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup, destination)
                    elif destination.exists():
                        destination.unlink()
                raise
        return checkpoint

    def branch(self, checkpoint_id: str) -> str:
        self._checkpoint_root(checkpoint_id)
        checkpoint = next(item for item in self.list() if item.checkpoint_id == checkpoint_id)
        return f"{checkpoint.session_id}-branch-{uuid.uuid4().hex[:8]}"

    def _checkpoint_root(self, checkpoint_id: str) -> Path:
        if not self._CHECKPOINT_ID.fullmatch(checkpoint_id):
            raise ValueError("invalid checkpoint ID")
        return self.state_root / checkpoint_id

    def _relative_path(self, path: str) -> str:
        requested = Path(path)
        value = self._safe_path(path)
        if requested.is_absolute():
            return value.relative_to(self.repository).as_posix()
        normalized = value.relative_to(self.repository).as_posix()
        if normalized != requested.as_posix():
            raise ValueError("checkpoint paths must be canonical repository-relative paths")
        return normalized

    def _safe_path(self, relative: str) -> Path:
        value = (self.repository / relative).resolve()
        if value == self.repository or self.repository not in value.parents:
            raise ValueError("checkpoint path escapes repository")
        return value
