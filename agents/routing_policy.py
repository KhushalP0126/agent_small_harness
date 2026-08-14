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
            measured_route = self._best_measured_route(group)
            if measured_route:
                return measured_route
            route = group.get("best_observed_route", "")
            if route and group.get("success_rate", 0) >= 0.5:
                return route
        return ""

    @staticmethod
    def _best_measured_route(group: dict) -> str:
        """Choose a reliable route, preferring lower observed cost on ties.

        Historic files without telemetry retain the legacy ``best_observed_route``
        fallback above. A route must first clear the same 50% success floor;
        cost never promotes an unreliable route merely because it is cheaper.
        """
        metrics = group.get("route_metrics", {})
        if not isinstance(metrics, dict):
            return ""
        candidates: list[tuple[float, float, float, str]] = []
        for route, sample in metrics.items():
            if not isinstance(route, str) or not isinstance(sample, dict):
                continue
            success_rate = float(sample.get("success_rate", 0.0))
            if success_rate < 0.5:
                continue
            has_cost = int(sample.get("cost_observations", 0)) > 0
            cost = float(sample.get("avg_estimated_cost_usd", 0.0)) if has_cost else float("inf")
            has_tokens = int(sample.get("token_observations", 0)) > 0
            tokens = float(sample.get("avg_total_model_tokens", 0.0)) if has_tokens else float("inf")
            candidates.append((-success_rate, cost, tokens, route))
        if not candidates:
            return ""
        return min(candidates)[3]

    def run(
        self,
        classification: dict,
        human_review: dict | None = None,
        stats: dict | None = None,
    ) -> AgentResult:
        return AgentResult(agent=self.name, payload=asdict(self.decide(classification, human_review, stats)))
