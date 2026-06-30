from __future__ import annotations

from agents.base import AgentResult, BaseAgent


class ScopeTrackerAgent(BaseAgent):
    name = "agent-2b-scope-tracker"

    def run(self, shared_state: dict | None = None) -> AgentResult:
        shared_state = shared_state or {}
        risky_globals = sorted(key for key in shared_state if key.isupper())
        return AgentResult(
            agent=self.name,
            payload={
                "constraint_rule": "Keep shared mutable globals out of generated code paths.",
                "flagged_globals": risky_globals,
            },
        )
