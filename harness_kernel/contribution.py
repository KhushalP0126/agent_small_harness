"""Deterministic provider contribution scheduling and API cost guards."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContributionSplit:
    qwen: int = 50
    api: int = 50

    def __post_init__(self) -> None:
        if min(self.qwen, self.api) < 0 or self.qwen + self.api != 100:
            raise ValueError("Qwen and API contributions must be non-negative and total 100")


class WeightedSchedule:
    """Smooth weighted round-robin with a stable session-dependent phase."""
    def __init__(self, split: ContributionSplit, session_id: str) -> None:
        self.split = split
        self.index = int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) % 100

    def provider_at(self, call_index: int) -> str:
        position = (self.index + call_index * 37) % 100
        return "qwen" if position < self.split.qwen else "api"

    def sequence(self, length: int) -> list[str]:
        return [self.provider_at(index) for index in range(max(0, length))]


@dataclass
class ContributionTelemetry:
    requested: ContributionSplit
    scheduled: list[str] = field(default_factory=list)
    actual: list[str] = field(default_factory=list)
    deviations: list[dict[str, str]] = field(default_factory=list)
    provider_failures: dict[str, int] = field(default_factory=lambda: {"qwen": 0, "api": 0})

    def record(self, scheduled: str, actual: str, fallback_reason: str = "") -> None:
        self.scheduled.append(scheduled)
        self.actual.append(actual)
        if actual != scheduled:
            if not fallback_reason:
                raise ValueError("provider deviations require an explicit fallback reason")
            self.deviations.append({"scheduled": scheduled, "actual": actual, "reason": fallback_reason})

    def snapshot(self) -> dict[str, object]:
        total = len(self.actual)
        counts = {name: self.actual.count(name) for name in ("qwen", "api")}
        return {
            "requested": {"qwen": self.requested.qwen, "api": self.requested.api},
            "scheduled": list(self.scheduled), "actual": list(self.actual),
            "counts": counts,
            "percentages": {name: (count * 100 / total if total else 0.0) for name, count in counts.items()},
            "provider_failures": dict(self.provider_failures),
            "deviations": list(self.deviations), "mixed_fallback_routed": bool(self.deviations),
        }


@dataclass
class ApiCostGuard:
    cap_usd: float
    spent_usd: float = 0.0

    def decision(self, estimated_call_usd: float, approved_overage: bool = False) -> str:
        if estimated_call_usd < 0:
            raise ValueError("estimated cost must be non-negative")
        if self.spent_usd + estimated_call_usd > self.cap_usd and not approved_overage:
            return "approval_required"
        return "allowed"

    def record(self, actual_cost_usd: float) -> None:
        if actual_cost_usd < 0:
            raise ValueError("actual cost must be non-negative")
        self.spent_usd += actual_cost_usd

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)
