from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngineDiagnostic:
    violation: str = ""
    threshold: str = ""
    actual: str = ""
    location: str = ""
    recommended_refactor: str = ""


@dataclass
class EngineFinding:
    engine: str
    severity: str
    summary: str
    details: str
    metrics: dict[str, Any] = field(default_factory=dict)
    diagnostic: EngineDiagnostic = field(default_factory=EngineDiagnostic)


class BaseEngine:
    name = "base-engine"

    def scan(self, source: str) -> list[EngineFinding]:
        raise NotImplementedError
