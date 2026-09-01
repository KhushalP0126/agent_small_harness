"""Small, deterministic helpers for comparing repair attempts.

The generation controller owns the workflow. This module owns only the pure
questions needed between attempts, which keeps those decisions independently
testable and keeps the main controller focused on orchestration.
"""

from __future__ import annotations

from difflib import unified_diff


def draft_diff(previous: str, current: str) -> str:
    """Return a stable unified diff, or an empty string for no change."""
    if previous == current:
        return ""
    return "\n".join(
        unified_diff(
            previous.splitlines(),
            current.splitlines(),
            fromfile="attempt_prev",
            tofile="attempt_curr",
            lineterm="",
        )
    )


def diagnostic_stagnant(deltas: list[dict]) -> bool:
    """Say whether every repeated diagnostic failed to improve."""
    repeated = [delta for delta in deltas if delta.get("repeated")]
    return bool(repeated) and all(not delta.get("improved", False) for delta in repeated)
