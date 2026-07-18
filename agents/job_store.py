from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


@dataclass
class JobRecord:
    job_id: str
    status: str
    target: str
    created_at: str
    events: list[dict[str, Any]] = field(default_factory=list)


class JsonlJobStore:
    """Append-only local job store for scalable queue-style orchestration."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def create_job(self, target: str, status: str = "queued") -> JobRecord:
        job = JobRecord(
            job_id=str(uuid.uuid4()),
            status=status,
            target=target,
            created_at=self._now(),
            events=[],
        )
        self._append({"type": "job_created", "job": asdict(job)})
        return job

    def append_event(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self._append(
            {
                "type": "job_event",
                "job_id": job_id,
                "event_type": event_type,
                "payload": payload,
                "recorded_at": self._now(),
            }
        )

    def update_status(self, job_id: str, status: str) -> None:
        self.append_event(job_id, "status", {"status": status})

    def get_job(self, job_id: str) -> JobRecord | None:
        job: JobRecord | None = None
        for entry in self._read_entries():
            if entry.get("type") == "job_created" and entry["job"]["job_id"] == job_id:
                job = JobRecord(**entry["job"])
            elif entry.get("type") == "job_event" and entry.get("job_id") == job_id and job is not None:
                event = {
                    "event_type": entry["event_type"],
                    "payload": entry["payload"],
                    "recorded_at": entry["recorded_at"],
                }
                job.events.append(event)
                if entry["event_type"] == "status":
                    job.status = str(entry["payload"].get("status", job.status))
        return job

    def _append(self, entry: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            with self._locked(handle, exclusive=True):
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def _read_entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            with self._locked(handle, exclusive=False):
                return [
                    json.loads(line)
                    for line in handle.read().splitlines()
                    if line.strip()
                ]

    @contextmanager
    def _locked(self, handle, *, exclusive: bool):
        if fcntl is not None:
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle.fileno(), operation)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
