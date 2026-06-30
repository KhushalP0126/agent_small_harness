from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LoopConstraint:
    max_depth: int
    deepest_path: list[str] = field(default_factory=list)
    mutation_sites: list[str] = field(default_factory=list)


@dataclass
class BranchConstraint:
    cyclomatic_complexity: int
    branch_count: int
    risk_level: str
    dominant_conditions: list[str] = field(default_factory=list)


@dataclass
class MutationConstraint:
    explicit_globals: list[str] = field(default_factory=list)
    module_level_mutations: list[str] = field(default_factory=list)
    shared_containers: list[str] = field(default_factory=list)


@dataclass
class ConstraintBlock:
    goal: str
    loops: LoopConstraint
    branches: BranchConstraint
    mutations: MutationConstraint
    conventions: list[str] = field(default_factory=list)
    dependency_context: list[str] = field(default_factory=list)
    lessons_learned: list[str] = field(default_factory=list)
