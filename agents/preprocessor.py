from __future__ import annotations

from pathlib import Path

from agents.base import AgentResult, BaseAgent
from agents.prompt_normalizer import PromptNormalizerAgent


class PreprocessorAgent(BaseAgent):
    name = "agent-4-preprocessor"

    def __init__(self, conventions_path: Path) -> None:
        self.conventions_path = conventions_path

    def run(self, gen_id: str, goal: str) -> AgentResult:
        normalized = PromptNormalizerAgent().normalize(goal)
        return AgentResult(
            agent=self.name,
            payload={
                "gen_id": gen_id,
                "raw_goal": goal,
                "goal": normalized.normalized_prompt,
                "normalization": {
                    "removed_fragments": normalized.removed_fragments,
                },
                "constraints": self.conventions_path.read_text(encoding="utf-8"),
            },
        )
