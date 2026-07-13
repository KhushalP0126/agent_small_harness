from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from agents.base import AgentResult, BaseAgent


class HistorianAgent(BaseAgent):
    """Loads lessons, records generations, and learns from repair sessions.

    Beyond plain logging, the historian now summarizes failed attempts, records which
    templates/prompts produced a compliant draft, and promotes successful repairs into
    reusable ``lessons_learned`` entries that ``run()`` can feed back into the coder.
    All writes are additive so the existing history schema stays intact.
    """

    name = "agent-5-historian"

    def __init__(self, history_path: Path) -> None:
        self.history_path = history_path

    def _load(self) -> dict:
        return json.loads(self.history_path.read_text(encoding="utf-8"))

    def _save(self, history: dict) -> None:
        self.history_path.write_text(
            json.dumps(history, indent=2) + "\n",
            encoding="utf-8",
        )

    def run(self, gen_id: str) -> AgentResult:
        history = self._load()
        lessons = [
            entry["lesson"]
            for entry in history.get("lessons_learned", [])
            if entry.get("gen_id") == gen_id or entry.get("gen_id") == "day1-bootstrap"
        ]
        return AgentResult(agent=self.name, payload={"lessons_learned": lessons})

    def append_generation(self, generation_record: dict) -> None:
        history = self._load()
        history.setdefault("generations", []).append(generation_record)
        self._save(history)

    def summarize_session(self, session: dict) -> dict:
        """Condense a controller session into a compact failure/outcome summary."""
        attempts = session.get("attempts", [])
        failed_attempts: list[dict[str, Any]] = []
        for attempt in attempts:
            validation = attempt.get("validation", {})
            behavior = attempt.get("behavior_validation", {})
            if validation.get("is_compliant", True) and behavior.get("is_compliant", True):
                continue
            failed_attempts.append(
                {
                    "attempt": attempt.get("attempt"),
                    "static_violations": [
                        violation.get("kind") for violation in validation.get("violations", [])
                    ],
                    "behavior_issues": [issue.get("case") for issue in behavior.get("issues", [])],
                }
            )
        return {
            "target": session.get("target"),
            "route": session.get("route"),
            "final_status": session.get("final_status"),
            "attempts": len(attempts),
            "failed_attempts": failed_attempts,
        }

    def record_repair_outcome(
        self,
        gen_id: str,
        session: dict,
        template_name: str = "",
        prompt_label: str = "",
    ) -> dict:
        """Persist what happened in a repair session and learn from a success."""
        history = self._load()
        summary = self.summarize_session(session)
        succeeded = session.get("final_status") == "completed"
        outcome = {
            "gen_id": gen_id,
            "recorded_at": self._now(),
            "final_status": session.get("final_status"),
            "template": template_name,
            "prompt_label": prompt_label,
            "succeeded": succeeded,
            "summary": summary,
        }
        history.setdefault("repair_outcomes", []).append(outcome)
        if succeeded and template_name:
            self._register_successful_template(history, gen_id, template_name, prompt_label)
        self._save(history)
        return outcome

    def successful_templates(self) -> dict[str, int]:
        """Return how many times each template produced a compliant draft."""
        history = self._load()
        counts: dict[str, int] = {}
        for outcome in history.get("repair_outcomes", []):
            template = outcome.get("template")
            if outcome.get("succeeded") and template:
                counts[template] = counts.get(template, 0) + 1
        return counts

    def build_run_record(
        self,
        session: dict,
        classification: dict | None = None,
        route_used: str = "",
        model: str = "",
        template_name: str = "",
    ) -> dict:
        """Create a normalized labeled sample from a controller session."""
        classification = classification or {}
        attempts = session.get("attempts", [])
        failed_engines: list[str] = []
        failed_kinds: list[str] = []
        behavior_failures = 0
        behavior_passed_static_blocked = False
        for attempt in attempts:
            validation = attempt.get("validation", {})
            behavior = attempt.get("behavior_validation", {})
            for violation in validation.get("violations", []):
                engine = violation.get("engine")
                kind = violation.get("kind")
                if engine:
                    failed_engines.append(engine)
                if kind:
                    failed_kinds.append(kind)
            behavior_failures += len(behavior.get("issues", []))
            if (
                behavior.get("is_compliant", True)
                and not validation.get("is_compliant", True)
                and validation.get("violations", [])
            ):
                behavior_passed_static_blocked = True
        return {
            "recorded_at": self._now(),
            "target": session.get("target", ""),
            "task_type": classification.get("task_type", "unknown"),
            "language": classification.get("language", "unknown"),
            "libraries": classification.get("libraries", []),
            "route_used": route_used or session.get("route", ""),
            "model": model,
            "template": template_name,
            "repair_attempts": max(0, len(attempts) - 1),
            "final_status": session.get("final_status", "unknown"),
            "failed_engines": sorted(set(failed_engines)),
            "failed_kinds": sorted(set(failed_kinds)),
            "behavior_failures": behavior_failures,
            "behavior_passed_static_blocked": behavior_passed_static_blocked,
            "human_review_reason": (session.get("human_review") or {}).get("reason", ""),
        }

    def regression_report(self, run_record: dict, historical_records: list[dict]) -> dict:
        """Compare a run against same-shape historical records."""
        peers = [
            record
            for record in historical_records
            if record is not run_record and self._same_shape(record, run_record)
        ]
        if len(peers) < 2:
            return {"regressed": False, "reason": "insufficient_history", "peer_count": len(peers)}
        peer_repairs = [int(record.get("repair_attempts", 0)) for record in peers]
        peer_failures = [len(record.get("failed_kinds", [])) for record in peers]
        median_repairs = float(median(peer_repairs))
        median_failures = float(median(peer_failures))
        current_repairs = int(run_record.get("repair_attempts", 0))
        current_failures = len(run_record.get("failed_kinds", []))
        status_regressed = (
            any(record.get("final_status") == "completed" for record in peers)
            and run_record.get("final_status") != "completed"
        )
        repair_regressed = current_repairs > median_repairs + 1
        failure_regressed = current_failures > median_failures + 1
        reasons = []
        if status_regressed:
            reasons.append("final_status_worse_than_successful_peers")
        if repair_regressed:
            reasons.append("repair_attempts_above_historical_median")
        if failure_regressed:
            reasons.append("failure_kinds_above_historical_median")
        return {
            "regressed": bool(reasons),
            "reasons": reasons,
            "peer_count": len(peers),
            "median_repair_attempts": median_repairs,
            "current_repair_attempts": current_repairs,
            "median_failure_kinds": median_failures,
            "current_failure_kinds": current_failures,
        }

    def append_run_sample(self, runs_path: Path, run_record: dict) -> None:
        """Append one raw run sample to a JSONL file."""
        runs_path.parent.mkdir(parents=True, exist_ok=True)
        with runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(run_record, sort_keys=True) + "\n")

    def aggregate_run_stats(self, runs_path: Path, stats_path: Path | None = None) -> dict:
        """Aggregate raw JSONL samples into route stats keyed by task/library/language."""
        records = self._load_run_samples(runs_path)
        stats = {"schema_version": "1.0", "groups": {}}
        grouped: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            keys = [
                f"task_type:{record.get('task_type', 'unknown')}",
                f"language:{record.get('language', 'unknown')}",
            ]
            keys.extend(f"library:{library}" for library in record.get("libraries", []))
            for key in keys:
                grouped[key].append(record)
        for key, group in grouped.items():
            total = len(group)
            completed = sum(1 for record in group if record.get("final_status") == "completed")
            repair_attempts = sum(int(record.get("repair_attempts", 0)) for record in group)
            failed_engines = Counter(engine for record in group for engine in record.get("failed_engines", []))
            failed_kinds = Counter(kind for record in group for kind in record.get("failed_kinds", []))
            routes = Counter(record.get("route_used", "") for record in group if record.get("route_used"))
            contribution_labels = Counter(
                record.get("contribution", {}).get("label", "")
                for record in group
                if isinstance(record.get("contribution"), dict)
                and record.get("contribution", {}).get("label")
            )
            contribution_scores = [
                float(record.get("contribution", {}).get("score", 0.0))
                for record in group
                if isinstance(record.get("contribution"), dict)
            ]
            behavior_static_blocks = sum(
                1 for record in group if record.get("behavior_passed_static_blocked")
            )
            stats["groups"][key] = {
                "total_runs": total,
                "completed_runs": completed,
                "success_rate": 0.0 if total == 0 else completed / total,
                "avg_repair_attempts": 0.0 if total == 0 else repair_attempts / total,
                "avg_contribution_score": (
                    0.0 if not contribution_scores else sum(contribution_scores) / len(contribution_scores)
                ),
                "behavior_passed_static_blocked_runs": behavior_static_blocks,
                "behavior_passed_static_blocked_rate": (
                    0.0 if total == 0 else behavior_static_blocks / total
                ),
                "top_contribution": contribution_labels.most_common(1)[0][0] if contribution_labels else "",
                "top_failed_engine": failed_engines.most_common(1)[0][0] if failed_engines else "",
                "top_failure_kind": failed_kinds.most_common(1)[0][0] if failed_kinds else "",
                "best_observed_route": routes.most_common(1)[0][0] if routes else "",
            }
        if stats_path is not None:
            stats_path.parent.mkdir(parents=True, exist_ok=True)
            stats_path.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return stats

    @staticmethod
    def _same_shape(left: dict, right: dict) -> bool:
        return (
            left.get("task_type") == right.get("task_type")
            and left.get("language") == right.get("language")
            and sorted(left.get("libraries", [])) == sorted(right.get("libraries", []))
        )

    def _load_run_samples(self, runs_path: Path) -> list[dict]:
        if not runs_path.exists():
            return []
        return [json.loads(line) for line in runs_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _register_successful_template(
        self,
        history: dict,
        gen_id: str,
        template_name: str,
        prompt_label: str,
    ) -> None:
        lessons = history.setdefault("lessons_learned", [])
        lesson_id = f"repair-template-{template_name}"
        for entry in lessons:
            if entry.get("id") == lesson_id:
                entry["success_count"] = entry.get("success_count", 0) + 1
                applies_to = entry.setdefault("applies_to", [])
                if gen_id not in applies_to:
                    applies_to.append(gen_id)
                return
        via = f" via {prompt_label}" if prompt_label else ""
        lessons.append(
            {
                "id": lesson_id,
                "gen_id": gen_id,
                "category": "repair",
                "pattern": f"{template_name} repair template",
                "lesson": (
                    f"Template-directed repair with '{template_name}' produced a compliant "
                    f"draft{via}. Prefer it when this pattern is detected."
                ),
                "applies_to": [gen_id, template_name],
                "created_at": self._now(),
                "success_count": 1,
            }
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
