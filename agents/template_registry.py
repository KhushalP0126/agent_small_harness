from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from agents.task_classifier import TaskClassification
from agents.template_loader import TemplateLibrary
from harness_kernel.task_ir import TemplateRoute


@dataclass(frozen=True)
class TemplateMatch:
    name: str
    language: str
    source: str
    reason: str

    def to_route(self) -> TemplateRoute:
        return TemplateRoute(
            name=self.name,
            language=self.language,
            source=self.source,
            reason=self.reason,
        )


class TemplateRegistry:
    """Selects known skeletons from an injected route table.

    The registry is deliberately deterministic and does not generate code. It
    also does not embed product-specific app routes in the harness. Callers can
    inject route mappings from configuration, experiments, or a future UI.
    """

    def __init__(
        self,
        library: TemplateLibrary | None = None,
        routes: Mapping[tuple[str, str], str] | None = None,
    ) -> None:
        self.library = library or TemplateLibrary()
        self.routes = dict(routes or {})

    def select(self, prompt: str, classification: TaskClassification) -> TemplateMatch | None:
        del prompt
        route_name = self.routes.get((classification.task_type, classification.language))
        if route_name:
            return self._load(
                route_name,
                classification.language,
                f"configured route for {classification.task_type}/{classification.language}",
            )
        return None

    def _load(self, name: str, language: str, reason: str) -> TemplateMatch | None:
        source = self.library.load(name, language)
        if source is None:
            return None
        return TemplateMatch(name=name, language=language, source=source, reason=reason)
