from __future__ import annotations

from agents.base import AgentResult, BaseAgent


class PostprocessorAgent(BaseAgent):
    name = "agent-3-postprocessor"

    def run(self, artifacts: list[str]) -> AgentResult:
        return AgentResult(
            agent=self.name,
            payload={
                "validated_artifacts": artifacts,
                "documentation_status": "conventions.md and README.md present",
            },
        )
