from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from harness_kernel.provenance import collect_provenance, configured_model_settings
from prompt.budget import estimate_tokens


# Version 3 adds the secret-free provenance manifest to every persisted run.
ARTIFACT_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class ArtifactPaths:
    run_id: str
    run_dir: Path


def new_run_id(prefix: str = "run") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{timestamp}-{uuid4().hex[:8]}"


class ArtifactManager:
    """Writes final artifacts and resumable in-progress checkpoints."""

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
        metadata = dict(metadata or {})
        metadata.setdefault(
            "provenance",
            collect_provenance(
                repository_root=Path.cwd(),
                settings=configured_model_settings(),
            ),
        )
        self._write_json(
            paths.run_dir / "metadata.json",
            {"schema_version": ARTIFACT_SCHEMA_VERSION, "run_id": paths.run_id, **metadata, "telemetry": self._telemetry(session, metadata)},
        )
        self._write_json(paths.run_dir / "session_summary.json", {"schema_version": ARTIFACT_SCHEMA_VERSION, **self._session_summary(session)})
        # Keep the historical timeline array shape for existing readers; the
        # versioned metadata/session/validation artifacts carry the schema.
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
                    "schema_version": ARTIFACT_SCHEMA_VERSION,
                    "validation": attempt.get("validation", {}),
                    "behavior_validation": attempt.get("behavior_validation", {}),
                    "execution_trace": attempt.get("execution_trace", {}),
                    "profiling_validation": attempt.get(
                        "profiling_validation", {}
                    ),
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

    def checkpoint(self, session: dict[str, Any], paths: ArtifactPaths) -> Path:
        """Atomically persist the controller's resumable state."""

        checkpoint_path = paths.run_dir / "checkpoint.json"
        temporary_path = paths.run_dir / ".checkpoint.json.tmp"
        temporary_path.write_text(
            json.dumps(session, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(checkpoint_path)
        return checkpoint_path

    def load_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        """Load a run checkpoint, returning ``None`` when it does not exist."""

        checkpoint_path = self.root / run_id / "checkpoint.json"
        if not checkpoint_path.is_file():
            return None
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Checkpoint for '{run_id}' must contain a JSON object")
        return payload

    def list_runs(self) -> list[str]:
        """Return checkpointed run IDs ordered from most to least recent."""

        if not self.root.is_dir():
            return []
        checkpoint_paths = [
            path / "checkpoint.json"
            for path in self.root.iterdir()
            if path.is_dir() and (path / "checkpoint.json").is_file()
        ]
        checkpoint_paths.sort(
            key=lambda path: (path.stat().st_mtime, path.parent.name),
            reverse=True,
        )
        return [path.parent.name for path in checkpoint_paths]

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
            profiling = attempt.get("profiling_validation", {})
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
                    "profiling_enabled": bool(profiling.get("enabled", False)),
                    "profiling_compliant": bool(
                        profiling.get("is_compliant", True)
                    ),
                    "profiling_issues": len(profiling.get("issues", [])),
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
        priced_costs = [
            float(call["estimated_cost_usd"])
            for call in model_calls
            if isinstance(call.get("estimated_cost_usd"), (int, float))
            and not isinstance(call.get("estimated_cost_usd"), bool)
        ]
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
            "estimated_model_cost_usd": sum(priced_costs) if priced_costs else None,
            "priced_model_call_count": len(priced_costs),
            "unpriced_model_call_count": sum(
                1
                for call in model_calls
                if not isinstance(call.get("estimated_cost_usd"), (int, float))
                or isinstance(call.get("estimated_cost_usd"), bool)
            ),
            "model_calls": model_calls,
        }

    def _write_json(self, path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
