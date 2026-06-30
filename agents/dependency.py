from __future__ import annotations

from pathlib import Path

from agents.base import AgentResult, BaseAgent


class DependencyAgent(BaseAgent):
    name = "agent-2a-dependency"

    def run(self, project_root: Path) -> AgentResult:
        requirements = [
            path.name
            for path in project_root.iterdir()
            if path.name in {"requirements.txt", "pyproject.toml", "setup.py"}
        ]
        return AgentResult(
            agent=self.name,
            payload={
                "dependency_files": requirements,
                "context_hint": "No external dependencies required for Day 1 scaffold."
                if not requirements
                else "Validate engine findings against declared project dependencies.",
            },
        )
