from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ShieldTaskTokens:
    task: str
    baseline_tokens: int
    shielded_tokens: int

    @property
    def delta(self) -> int:
        return self.baseline_tokens - self.shielded_tokens


@dataclass(frozen=True)
class ComputeShieldMetrics:
    phase: int
    tokens_baseline: int
    tokens_shielded: int
    delta: int
    tasks: tuple[ShieldTaskTokens, ...]

    def to_event(self) -> dict[str, Any]:
        return {
            "type": "compute_shield_metrics",
            **asdict(self),
        }


def compute_shield_metrics(
    tasks: Iterable[ShieldTaskTokens],
    *,
    phase: int = 3,
) -> ComputeShieldMetrics:
    rows = tuple(tasks)
    if phase not in {1, 2, 3}:
        raise ValueError("phase must be 1, 2, or 3")
    if any(row.baseline_tokens < 0 or row.shielded_tokens < 0 for row in rows):
        raise ValueError("token counts cannot be negative")
    baseline = sum(row.baseline_tokens for row in rows)
    shielded = sum(row.shielded_tokens for row in rows)
    return ComputeShieldMetrics(
        phase=phase,
        tokens_baseline=baseline,
        tokens_shielded=shielded,
        delta=baseline - shielded,
        tasks=rows,
    )


def tokens_from_telemetry(payload: dict[str, Any]) -> int:
    """Return exact recorded tokens from an artifact metadata payload."""

    telemetry = payload.get("telemetry", payload)
    calls = telemetry.get("model_calls", [])
    if calls:
        return sum(int(call.get("total_tokens", 0)) for call in calls)
    return int(telemetry.get("total_model_tokens", 0))


def shield_task_from_artifacts(
    task: str,
    baseline_metadata: dict[str, Any],
    shielded_metadata: dict[str, Any],
) -> ShieldTaskTokens:
    """Build one comparison row from existing ArtifactManager metadata."""

    return ShieldTaskTokens(
        task=task,
        baseline_tokens=tokens_from_telemetry(baseline_metadata),
        shielded_tokens=tokens_from_telemetry(shielded_metadata),
    )
