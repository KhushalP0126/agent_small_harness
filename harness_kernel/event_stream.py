"""Optional inherited event channel for subprocess-driven harness clients."""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable


EVENT_FD_ENV = "HARNESS_EVENT_FD"
_WRITE_LOCK = threading.Lock()


def event_sink_from_env() -> Callable[[dict[str, Any]], None] | None:
    """Return a JSONL event writer when the parent supplied a dedicated fd."""

    raw_fd = os.environ.get(EVENT_FD_ENV, "").strip()
    if not raw_fd:
        return None
    try:
        fd = int(raw_fd)
    except ValueError:
        return None

    def emit(event: dict[str, Any]) -> None:
        encoded = (
            json.dumps(event, separators=(",", ":"), default=str) + "\n"
        ).encode("utf-8")
        with _WRITE_LOCK:
            try:
                os.write(fd, encoded)
            except OSError:
                # A closed review client must never fail the harness run.
                return

    return emit
