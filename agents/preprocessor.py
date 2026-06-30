from __future__ import annotations

from pathlib import Path

from agents.base import AgentResult, BaseAgent


class PreprocessorAgent(BaseAgent):
    name = "agent-4-preprocessor"

    def __init__(self, conventions_path: Path) -> None:
        self.conventions_path = conventions_path

    def run(self, gen_id: str, goal: str) -> AgentResult:
        return AgentResult(
            agent=self.name,
            payload={
                "gen_id": gen_id,
                "goal": goal,
                "constraints": self.conventions_path.read_text(encoding="utf-8"),
            },
        )
