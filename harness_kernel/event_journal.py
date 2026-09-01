"""Sanitized append-only orchestration journal and content-addressed blobs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator


_SECRET_KEYS = re.compile(r"(secret|token|password|credential|api[_-]?key)", re.I)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): ("[REDACTED]" if _SECRET_KEYS.search(str(key)) else redact(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _BEARER.sub("Bearer [REDACTED]", value)
    return value


@dataclass(frozen=True)
class JournalEvent:
    sequence: int
    session_id: str
    graph_revision: int
    node_id: str | None
    attempt_id: str | None
    parent_event: int | None
    event_type: str
    payload: dict[str, Any]


class EventJournal:
    def __init__(self, root: Path, session_id: str) -> None:
        self.root = root.resolve()
        self.session_id = session_id
        self.path = self.root / "events.jsonl"
        self.blob_root = self.root / "blobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.blob_root.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        events = list(self.read(verify=True)) if self.path.exists() else []
        self._sequence = events[-1].sequence if events else 0

    def put_blob(self, content: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        path = self.blob_root / digest
        if not path.exists():
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(content)
            os.replace(temporary, path)
        return {"sha256": digest, "size": len(content)}

    def get_blob(self, reference: dict[str, Any]) -> bytes:
        digest = reference["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid blob hash")
        content = (self.blob_root / digest).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest or len(content) != reference["size"]:
            raise ValueError(f"blob integrity failure: {digest}")
        return content

    def append(self, event_type: str, payload: dict[str, Any], *, graph_revision: int,
               node_id: str | None = None, attempt_id: str | None = None,
               parent_event: int | None = None) -> JournalEvent:
        with self._lock:
            self._sequence += 1
            event = JournalEvent(self._sequence, self.session_id, graph_revision, node_id,
                                 attempt_id, parent_event, event_type, redact(payload))
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(asdict(event), sort_keys=True, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return event

    def read(self, *, verify: bool = True) -> Iterator[JournalEvent]:
        expected = 1
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                event = JournalEvent(**json.loads(line))
                if verify and (event.sequence != expected or event.session_id != self.session_id):
                    raise ValueError("event journal sequence or session mismatch")
                expected += 1
                yield event

    def replay(self) -> Iterator[JournalEvent]:
        """Pure replay: reading and integrity checks are the only operations."""
        yield from self.read(verify=True)
