from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class AgentResult:
    agent: str
    payload: Dict[str, Any]


class BaseAgent:
    name = "base-agent"

    def run(self, **kwargs: Any) -> AgentResult:
        raise NotImplementedError
