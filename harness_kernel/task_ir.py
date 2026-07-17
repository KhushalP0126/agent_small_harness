from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TemplateRoute:
    """A known skeleton route selected before model execution."""

    name: str = ""
    language: str = "python"
    source: str = ""
    reason: str = ""


@dataclass
class ValidationPlan:
    """Validation requirements the execution kernel must enforce."""

    engines: list[str] = field(default_factory=list)
    behavior_examples: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    formal_checks: list[str] = field(default_factory=list)


@dataclass
class TaskIR:
    """Structured task input consumed by the execution kernel.

    Plan Mode and future UI/frontends should produce this shape. The kernel
    should not need to know whether the task came from a form, CLI prompt, repo
    audit, or template-backed generator.
    """

    goal: str
    task_type: str
    language: str
    app_name: str = ""
    game_kind: str = ""
    kernel_mode: str = "generate_from_spec"
    target_function: str = ""
    route_hint: str = "small_worker"
    template: TemplateRoute | None = None
    files: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    allowed_libraries: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    state: list[str] = field(default_factory=list)
    dependency_graph: list[str] = field(default_factory=list)
    validation: ValidationPlan = field(default_factory=ValidationPlan)
