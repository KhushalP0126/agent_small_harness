from __future__ import annotations

from dataclasses import asdict, dataclass, field

from agents.base import AgentResult, BaseAgent


@dataclass
class TaskClassification:
    task_type: str
    language: str
    needs_behavior_spec: bool
    libraries: list[str] = field(default_factory=list)
    route_hint: str = "small_worker"


class TaskClassifierAgent(BaseAgent):
    name = "agent-task-classifier"

    def classify(self, prompt: str) -> TaskClassification:
        lowered = prompt.lower()
        language = self._language(lowered)
        libraries = [name for name in ("pygame", "pandas", "sqlalchemy") if name in lowered]
        task_type = self._task_type(lowered, libraries)
        needs_behavior = any(token in lowered for token in ("function", "return", "input", "output", "api"))
        route_hint = "template_or_small_worker" if task_type in {"game", "data"} else "small_worker"
        return TaskClassification(
            task_type=task_type,
            language=language,
            needs_behavior_spec=needs_behavior,
            libraries=libraries,
            route_hint=route_hint,
        )

    def _language(self, lowered: str) -> str:
        if "c++" in lowered or "cpp" in lowered:
            return "cpp"
        if " c " in f" {lowered} " or "c11" in lowered:
            return "c"
        return "python"

    def _task_type(self, lowered: str, libraries: list[str]) -> str:
        if "game" in lowered or "pygame" in libraries:
            return "game"
        if "data" in lowered or "pandas" in libraries:
            return "data"
        if "sql" in lowered or "sqlalchemy" in libraries:
            return "database"
        if "repair" in lowered or "fix" in lowered or "refactor" in lowered:
            return "repair"
        return "general_code"

    def run(self, prompt: str) -> AgentResult:
        return AgentResult(agent=self.name, payload=asdict(self.classify(prompt)))
