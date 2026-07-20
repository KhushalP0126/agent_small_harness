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
    reports what the draft actually did (return values, exceptions, timeouts)
    versus what the behavior examples expect. It is intentionally bounded; the
    full debugger protocol (stepping, state diffing across contracts) is a
    follow-up that can build on the same :class:`ExecutionTrace`.
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
