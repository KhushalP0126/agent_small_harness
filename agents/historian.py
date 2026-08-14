from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from agents.base import AgentResult, BaseAgent


DEFAULT_HISTORY_PATH = Path(__file__).resolve().parents[1] / "history.json"
_MATCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "write",
    "create",
    "build",
    "generate",
}


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _nonnegative_float(value: object) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _route_metrics(records: list[dict]) -> dict:
    """Summarize observed outcome and cost for one execution route."""
    total = len(records)
    completed = sum(1 for record in records if record.get("final_status") == "completed")
    costs = [
        _nonnegative_float(record.get("estimated_model_cost_usd"))
        for record in records
        if "estimated_model_cost_usd" in record
    ]
    tokens = [
        _nonnegative_int(record.get("total_model_tokens"))
        for record in records
        if "total_model_tokens" in record
    ]
    return {
        "total_runs": total,
        "success_rate": 0.0 if total == 0 else completed / total,
        "cost_observations": len(costs),
        "avg_estimated_cost_usd": 0.0 if not costs else sum(costs) / len(costs),
        "token_observations": len(tokens),
        "avg_total_model_tokens": 0.0 if not tokens else sum(tokens) / len(tokens),
    }


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

    def similar_past_attempts(
        self,
        task_signature: str,
        *,
        limit: int = 3,
        minimum_score: float = 0.25,
    ) -> list[dict[str, Any]]:
        """Return a bounded lexical match over durable local run history.

        This is intentionally deterministic and dependency-free. Matches are
        advisory prompt context; they do not alter validation or acceptance.
        """

        if limit <= 0:
            return []
        query_tokens = self._signature_tokens(task_signature)
        if not query_tokens:
            return []
        history = self._load()
        candidates: list[dict[str, Any]] = []
        for outcome in history.get("repair_outcomes", []):
            summary = outcome.get("summary") or {}
            signature = str(summary.get("target") or outcome.get("prompt_label") or "")
            candidates.append(
                {
                    "source": "repair_outcome",
                    "signature": signature,
                    "final_status": str(outcome.get("final_status", "")),
                    "failure_kinds": sorted(
                        {
                            kind
                            for attempt in summary.get("failed_attempts", [])
                            for kind in attempt.get("static_violations", [])
                            if kind
                        }
                    ),
                    "lesson": (
                        f"Template {outcome.get('template')} succeeded."
                        if outcome.get("succeeded") and outcome.get("template")
                        else ""
                    ),
                }
            )
        for generation in history.get("generations", []):
            benchmark = generation.get("benchmark") or {}
            candidates.append(
                {
                    "source": "generation",
                    "signature": str(
                        generation.get("goal") or generation.get("gen_id") or ""
                    ),
                    "final_status": str(generation.get("final_status", "")),
                    "failure_kinds": sorted(
                        {
                            str(finding.get("engine", ""))
                            for finding in generation.get("engine_findings", [])
                            if finding.get("engine")
                        }
                    ),
                    "lesson": str(benchmark.get("classification", "")),
                }
            )
        for lesson in history.get("lessons_learned", []):
            candidates.append(
                {
                    "source": "lesson",
                    "signature": " ".join(
                        [
                            str(lesson.get("pattern", "")),
                            " ".join(str(item) for item in lesson.get("applies_to", [])),
                        ]
                    ).strip(),
                    "final_status": "learned",
                    "failure_kinds": [],
                    "lesson": str(lesson.get("lesson", "")),
                }
            )

        matches: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_tokens = self._signature_tokens(candidate["signature"])
            shared = query_tokens & candidate_tokens
            if not shared:
                continue
            coverage = len(shared) / len(query_tokens)
            union = query_tokens | candidate_tokens
            score = 0.7 * coverage + 0.3 * (len(shared) / len(union))
            if len(shared) < 2 and coverage < 0.75:
                continue
            if score < minimum_score:
                continue
            matches.append(
                {
                    **candidate,
                    "score": round(score, 4),
                    "matched_terms": sorted(shared),
                }
            )
        matches.sort(
            key=lambda item: (
                -float(item["score"]),
                item["source"],
                item["signature"],
            )
        )
        return matches[: min(limit, 5)]

    @staticmethod
    def _signature_tokens(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9_]+", value.lower())
            if len(token) > 1 and token not in _MATCH_STOPWORDS
        }

    def build_run_record(
        self,
        session: dict,
        classification: dict | None = None,
        route_used: str = "",
        model: str = "",
        template_name: str = "",
        model_usage: dict | None = None,
    ) -> dict:
        """Create a normalized labeled sample from a controller session."""
        classification = classification or {}
        usage = model_usage or session.get("model_usage") or {}
        if not isinstance(usage, dict):
            usage = {}
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
            "total_model_tokens": _nonnegative_int(usage.get("total_tokens", 0)),
            "estimated_model_cost_usd": _nonnegative_float(
                usage.get("estimated_cost_usd", 0.0)
            ),
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
        if "regression_report" not in run_record:
            historical_records = self._load_run_samples(runs_path)
            run_record["regression_report"] = self.regression_report(run_record, historical_records)
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
            route_records: dict[str, list[dict]] = defaultdict(list)
            for record in group:
                route = record.get("route_used", "")
                if route:
                    route_records[route].append(record)
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
            regressions = sum(
                1
                for record in group
                if isinstance(record.get("regression_report"), dict)
                and record["regression_report"].get("regressed")
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
                "regressed_runs": regressions,
                "regression_rate": 0.0 if total == 0 else regressions / total,
                "top_contribution": contribution_labels.most_common(1)[0][0] if contribution_labels else "",
                "top_failed_engine": failed_engines.most_common(1)[0][0] if failed_engines else "",
                "top_failure_kind": failed_kinds.most_common(1)[0][0] if failed_kinds else "",
                "best_observed_route": routes.most_common(1)[0][0] if routes else "",
                "route_metrics": {
                    route: _route_metrics(records)
                    for route, records in sorted(route_records.items())
                },
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
