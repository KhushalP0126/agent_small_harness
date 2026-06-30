from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngineFinding:
    engine: str
    severity: str
    summary: str
    details: str
    metrics: dict[str, Any] = field(default_factory=dict)


class BaseEngine:
    name = "base-engine"

    def scan(self, source: str) -> list[EngineFinding]:
        raise NotImplementedError
