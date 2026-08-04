from __future__ import annotations

from validation.behavior import ExecutionTrace


_MAX_HINTS = 6


def build_debugger_hints(
    trace: ExecutionTrace | None,
    type_contracts: list[str] | None = None,
    max_hints: int = _MAX_HINTS,
) -> list[str]:
    """Diff observed runtime state against the spec sheet into targeted hints.

    This is the debugger-mode hook: instead of generic "it failed" feedback, it
    reports what the draft actually did (return values, exceptions, timeouts,
    and bounded before/after state deltas) versus what the behavior examples
    expect. Cross-contract localization is exposed separately through
    :func:`localize_contract_failure`.
    """

    if trace is None:
        return []

    hints: list[str] = []
    if trace.fatal_case:
        if trace.fatal_case == "timeout":
            hints.append(
                "Execution timed out before returning; suspect an unbounded loop or a missing base case."
            )
        elif trace.fatal_case == "load":
            hints.append(f"The draft did not load or run: {trace.fatal_details}")
        else:
            hints.append(
                f"A required symbol is missing ({trace.fatal_case}): {trace.fatal_details}"
            )
        return hints[:max_hints]

    for case in trace.cases:
        if case.exception_type:
            detail = case.exception_message or case.exception_type
            hints.append(
                f"Case '{case.name}': calling with args {case.args} raised "
                f"{case.exception_type}: {detail}. Handle this runtime path instead of raising."
            )
        if case.state_delta and len(hints) < max_hints:
            hints.append(f"Case '{case.name}' state delta:\n{case.state_delta}")
        elif not case.matched:
            hints.append(
                f"Case '{case.name}': input {case.args} produced {case.returned}, but the spec "
                f"expects {case.expected}. Reconcile the logic with the expected value."
            )
        if len(hints) >= max_hints:
            break

    if type_contracts and len(hints) < max_hints:
        hints.append(
            "Respect the accepted type/method contracts: " + "; ".join(type_contracts[:3])
        )
    return hints[:max_hints]


def localize_contract_failure(
    failed_contract: str,
    dependency_graph: dict[str, list[str]],
    validation_results: dict[str, bool],
) -> list[str]:
    """Return failed upstream contracts before blaming a dependent contract."""

    suspects: list[str] = []
    for dependency in dependency_graph.get(failed_contract, []):
        if not validation_results.get(dependency, False):
            suspects.append(dependency)
    return suspects
