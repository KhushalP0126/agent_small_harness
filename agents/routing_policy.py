from __future__ import annotations

from dataclasses import asdict, dataclass

from agents.base import AgentResult, BaseAgent


@dataclass
class RouteDecision:
    worker: str
    reason: str
    max_retries: int


class RoutingPolicyAgent(BaseAgent):
    name = "agent-routing-policy"

    def decide(
        self,
        classification: dict,
        human_review: dict | None = None,
        stats: dict | None = None,
    ) -> RouteDecision:
        if human_review:
            return RouteDecision(
                worker="architect_llm",
                reason=f"manual review escalation: {human_review.get('reason', 'unknown')}",
                max_retries=1,
            )
        if classification.get("state_machine_constraints"):
            return RouteDecision(
                worker="architect_after_one_small_attempt",
                reason="state-machine parser tasks escalate after one failed worker attempt",
                max_retries=1,
            )
        observed = self._observed_route(classification, stats or {})
        if observed:
            return RouteDecision(
                worker=observed,
                reason="selected from historian route statistics",
                max_retries=2,
            )
        route_hint = classification.get("route_hint", "small_worker")
        if route_hint == "template_or_small_worker":
            return RouteDecision(
                worker="template_then_small_llm",
                reason="task matches a known template-friendly category",
                max_retries=2,
            )
        return RouteDecision(worker="small_llm", reason="default low-cost worker route", max_retries=2)

    def _observed_route(self, classification: dict, stats: dict) -> str:
        groups = stats.get("groups", {}) if isinstance(stats, dict) else {}
        candidate_keys = [f"task_type:{classification.get('task_type', 'unknown')}"]
        candidate_keys.extend(f"library:{library}" for library in classification.get("libraries", []))
        candidate_keys.append(f"language:{classification.get('language', 'unknown')}")
        for key in candidate_keys:
            group = groups.get(key, {})
            route = group.get("best_observed_route", "")
            if route and group.get("success_rate", 0) >= 0.5:
                return route
        return ""

    def run(
        self,
        classification: dict,
        human_review: dict | None = None,
        stats: dict | None = None,
    ) -> AgentResult:
        return AgentResult(agent=self.name, payload=asdict(self.decide(classification, human_review, stats)))
