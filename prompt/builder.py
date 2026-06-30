from __future__ import annotations

from prompt.constraint_types import ConstraintBlock


def build_prompt(block: ConstraintBlock) -> str:
    sections = [
        f"GOAL: {block.goal}",
        "",
        "STRUCTURAL CONSTRAINTS:",
        f"  - Max loop nesting: {block.loops.max_depth}",
        f"  - Deepest loop path: {' > '.join(block.loops.deepest_path) or 'none'}",
        f"  - Loop mutation targets: {', '.join(block.loops.mutation_sites) or 'none'}",
        f"  - Cyclomatic complexity target: <= {block.branches.cyclomatic_complexity}",
        f"  - Conditional branch count: {block.branches.branch_count}",
        f"  - Branch risk level: {block.branches.risk_level}",
        f"  - Dominant conditions: {', '.join(block.branches.dominant_conditions) or 'none'}",
        f"  - Explicit globals in scope: {', '.join(block.mutations.explicit_globals) or 'none'}",
        f"  - Module-level mutation targets: {', '.join(block.mutations.module_level_mutations) or 'none'}",
        f"  - Shared module containers: {', '.join(block.mutations.shared_containers) or 'none'}",
        "",
        "CONVENTIONS:",
    ]
    sections.extend(f"  - {item}" for item in block.conventions)
    sections.extend(["", "LESSONS LEARNED:"])
    sections.extend(f"  - {item}" for item in (block.lessons_learned or ["none"]))
    sections.extend(["", "DEPENDENCY CONTEXT:"])
    sections.extend(f"  - {item}" for item in (block.dependency_context or ["No external dependencies required"]))
    sections.extend(
        [
            "",
            "INSTRUCTIONS:",
            "  - Generate code that satisfies the goal within the constraints above.",
            "  - Do not introduce additional loop nesting or new global mutations.",
            "  - Prefer single-pass, cache-friendly code paths where possible.",
        ]
    )
    return "\n".join(sections)
