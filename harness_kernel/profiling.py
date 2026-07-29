from __future__ import annotations

import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from engines.base import EngineFinding


@dataclass(frozen=True)
class ProfileResult:
    loop_order: str
    runtime_ns: int
    spread_ns: int
    cache_misses: int | None
    samples_ns: tuple[int, ...]

    def to_event(self) -> dict[str, Any]:
        return {"type": "profiling_result", **asdict(self)}


class AlgorithmicProfiler:
    """Repeatable callable profiler with an explicit noise floor.

    Cache misses remain ``None`` unless a platform-specific counter supplier is
    injected; wall-clock measurements never pretend to be hardware counters.
    """

    def __init__(self, *, repeats: int = 5, warmups: int = 1) -> None:
        if repeats < 3:
            raise ValueError("profiling requires at least three measured runs")
        self.repeats = repeats
        self.warmups = max(0, warmups)

    def measure(
        self,
        loop_order: str,
        operation: Callable[[], Any],
        *,
        cache_miss_supplier: Callable[[], int] | None = None,
    ) -> ProfileResult:
        for _ in range(self.warmups):
            operation()
        samples: list[int] = []
        for _ in range(self.repeats):
            started = time.perf_counter_ns()
            operation()
            samples.append(time.perf_counter_ns() - started)
        median_ns = int(statistics.median(samples))
        return ProfileResult(
            loop_order=loop_order,
            runtime_ns=median_ns,
            spread_ns=max(samples) - min(samples),
            cache_misses=(
                cache_miss_supplier() if cache_miss_supplier is not None else None
            ),
            samples_ns=tuple(samples),
        )

    @staticmethod
    def faster(
        first: ProfileResult,
        second: ProfileResult,
        *,
        minimum_margin: float = 0.05,
    ) -> ProfileResult | None:
        """Return a winner only when it clears the documented noise margin."""

        slower = max(first.runtime_ns, second.runtime_ns)
        if slower <= 0:
            return None
        margin = abs(first.runtime_ns - second.runtime_ns) / slower
        if margin < minimum_margin:
            return None
        return first if first.runtime_ns < second.runtime_ns else second

    def compare(
        self,
        first_order: str,
        first_operation: Callable[[], Any],
        second_order: str,
        second_operation: Callable[[], Any],
        *,
        selected_order: str,
        minimum_margin: float = 0.05,
    ) -> tuple[tuple[ProfileResult, ProfileResult], list[EngineFinding]]:
        """Profile two equivalent implementations and flag a slower selection.

        This is deliberately opt-in and callable-based: the harness supplies
        behaviorally equivalent operations, so the profiler remains
        task-agnostic and never guesses loop order from source text.
        """

        first = self.measure(first_order, first_operation)
        second = self.measure(second_order, second_operation)
        winner = self.faster(first, second, minimum_margin=minimum_margin)
        if winner is None or winner.loop_order == selected_order:
            return (first, second), []
        selected = first if first.loop_order == selected_order else second
        return (first, second), [
            EngineFinding(
                engine="engine-algorithmic-profiling",
                severity="High",
                summary="Selected implementation is measurably slower",
                details=(
                    f"{selected.loop_order} median {selected.runtime_ns}ns; "
                    f"{winner.loop_order} median {winner.runtime_ns}ns."
                ),
                metrics={
                    "selected_order": selected.loop_order,
                    "faster_order": winner.loop_order,
                    "selected_runtime_ns": selected.runtime_ns,
                    "faster_runtime_ns": winner.runtime_ns,
                    "minimum_margin": minimum_margin,
                },
            )
        ]
