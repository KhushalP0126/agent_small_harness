from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from prompt.budget import estimate_tokens


@dataclass(frozen=True)
class ArtifactPaths:
    run_id: str
    run_dir: Path


def new_run_id(prefix: str = "run") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{timestamp}-{uuid4().hex[:8]}"


class ArtifactManager:
    """Writes per-attempt artifacts for post-run inspection.

    The controller keeps the loop state in memory. This manager turns that state
    into a file trail after a run completes, so live model behavior can be audited
    without rerunning the model.
    """

    def __init__(self, root: Path | str = "artifacts/runs") -> None:
        self.root = Path(root)

    def create_run(self, run_id: str | None = None, prefix: str = "run") -> ArtifactPaths:
        run_id = run_id or new_run_id(prefix)
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        return ArtifactPaths(run_id=run_id, run_dir=run_dir)

    def save_session(
        self,
        session: dict[str, Any],
        paths: ArtifactPaths,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata = metadata or {}
        self._write_json(
            paths.run_dir / "metadata.json",
            {"run_id": paths.run_id, **metadata, "telemetry": self._telemetry(session, metadata)},
        )
        self._write_json(paths.run_dir / "session_summary.json", self._session_summary(session))
        self._write_json(paths.run_dir / "attempt_timeline.json", self._attempt_timeline(session))
        for attempt in session.get("attempts", []):
            attempt_index = int(attempt.get("attempt", 0))
            prefix = paths.run_dir / f"attempt_{attempt_index}"
            (prefix.with_suffix(".py")).write_text(attempt.get("draft", ""), encoding="utf-8")
            (paths.run_dir / f"attempt_{attempt_index}_retry_prompt.txt").write_text(
                attempt.get("retry_prompt", ""),
                encoding="utf-8",
            )
            self._write_json(
                paths.run_dir / f"attempt_{attempt_index}_validation.json",
                {
                    "validation": attempt.get("validation", {}),
                    "behavior_validation": attempt.get("behavior_validation", {}),
                    "formal_validation": attempt.get("formal_validation", {}),
                    "diagnostic_deltas": attempt.get("diagnostic_deltas", []),
                    "repair_directives": attempt.get("repair_directives", []),
                    "repair_worker": attempt.get("repair_worker", ""),
                    "repair_error": attempt.get("repair_error", ""),
                    "changed": attempt.get("changed", False),
                    "diff": attempt.get("diff", ""),
                    "branch_state_signature": attempt.get("branch_state_signature", {}),
                    "branch_loop": attempt.get("branch_loop", {}),
                    "backend_failure": attempt.get("backend_failure", {}),
                    "diagnostic_stagnant": attempt.get("diagnostic_stagnant", False),
                },
            )
            self._write_json(paths.run_dir / f"attempt_{attempt_index}_findings.json", attempt.get("findings", []))

    def _session_summary(self, session: dict[str, Any]) -> dict[str, Any]:
        attempts = session.get("attempts", [])
        return {
            "target": session.get("target", ""),
            "route": session.get("route", ""),
            "max_retries": session.get("max_retries", 0),
            "final_status": session.get("final_status", ""),
            "attempt_count": len(attempts),
            "human_review": session.get("human_review"),
        }

    def _attempt_timeline(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for attempt in session.get("attempts", []):
            validation = attempt.get("validation", {})
            behavior = attempt.get("behavior_validation", {})
            formal = attempt.get("formal_validation", {})
            rows.append(
                {
                    "attempt": attempt.get("attempt", 0),
                    "repair_worker": attempt.get("repair_worker", ""),
                    "static_compliant": bool(validation.get("is_compliant", True)),
                    "static_violations": len(validation.get("violations", [])),
                    "behavior_compliant": bool(behavior.get("is_compliant", True)),
                    "behavior_issues": len(behavior.get("issues", [])),
                    "formal_compliant": bool(formal.get("is_compliant", True)),
                    "formal_issues": len(formal.get("issues", [])),
                    "changed": bool(attempt.get("changed", False)),
                    "diff_chars": len(attempt.get("diff", "")),
                    "retry_prompt_chars": len(attempt.get("retry_prompt", "")),
                    "retry_prompt_tokens_estimate": estimate_tokens(attempt.get("retry_prompt", "")),
                }
            )
        return rows

    def _telemetry(self, session: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        attempts = session.get("attempts", [])
        retry_prompt_chars = [len(attempt.get("retry_prompt", "")) for attempt in attempts]
        draft_chars = [len(attempt.get("draft", "")) for attempt in attempts]
        model_calls = metadata.get("model_telemetry", [])
        return {
            "attempt_count": len(attempts),
            "total_retry_prompt_chars": sum(retry_prompt_chars),
            "total_retry_prompt_tokens_estimate": sum(
                estimate_tokens(attempt.get("retry_prompt", "")) for attempt in attempts
            ),
            "max_retry_prompt_chars": max(retry_prompt_chars, default=0),
            "total_draft_chars": sum(draft_chars),
            "total_model_prompt_tokens": sum(int(call.get("prompt_tokens", 0)) for call in model_calls),
            "total_model_completion_tokens": sum(int(call.get("completion_tokens", 0)) for call in model_calls),
            "total_model_tokens": sum(int(call.get("total_tokens", 0)) for call in model_calls),
            "estimated_model_cost_usd": sum(float(call.get("estimated_cost_usd", 0.0)) for call in model_calls),
            "model_calls": model_calls,
        }

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
