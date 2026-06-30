from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
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
