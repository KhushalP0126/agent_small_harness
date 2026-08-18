"""Route Python formal-repair prompts by reproducible failure signature.

This module deliberately recognizes only failures that have been diagnosed from
recorded CrossHair transcripts.  Unknown failures do not receive a generic
formal-repair instruction: they retain the baseline prompt so a new broad
directive cannot silently create a regression.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from validation.types import Violation


UNCLASSIFIED = "unclassified"
NONNEGATIVE_RAISE = "nonnegative_defensive_raise"
ORDER_PAIR_REJECTION = "order_pair_input_rejection"
TRIM_TEXT_METHOD = "trim_text_wrong_method"


@dataclass(frozen=True)
class FormalRepairRoute:
    """A narrow, transcript-backed formal-repair decision."""

    signature_id: str
    directive: str = ""
    rationale: str = "No diagnosed failure signature matched."

    @property
    def classified(self) -> bool:
        return self.signature_id != UNCLASSIFIED


def route_formal_repair(source: str, violation: Violation | None) -> FormalRepairRoute:
    """Classify one CrossHair violation without guessing beyond known patterns."""

    witness = _counterexample(violation)
    normalized_source = source.casefold()
    normalized_witness = witness.casefold()

    if "post: _ >= 0" in normalized_source and re.search(r"\w+\(-\d+\)", normalized_witness):
        return FormalRepairRoute(
            NONNEGATIVE_RAISE,
            "The shown negative input is valid. Do NOT write `if value < 0: raise ...`; "
            "return a non-negative value instead, for example `return max(value, 0)`.",
            "A postcondition requires a non-negative return value; it does not restrict input values.",
        )

    if "post: _[0] <= _[1]" in normalized_source and _ordered_pair_is_reversed(normalized_witness):
        return FormalRepairRoute(
            ORDER_PAIR_REJECTION,
            "The shown reversed pair is valid input. Do NOT assert, raise, or reject it; "
            "return the values in ascending order, for example `return (min(left, right), max(left, right))`.",
            "The contract constrains the returned pair, not the ordering of the input arguments.",
        )

    if "post: _ == text.strip()" in normalized_source and "trim_text(" in normalized_witness:
        return FormalRepairRoute(
            TRIM_TEXT_METHOD,
            "Return `text.strip()` exactly. Do not use `lstrip()` or `rstrip()` because both leading "
            "and trailing whitespace are part of the postcondition.",
            "The string-normalization contract requires both ends of the string to be stripped.",
        )

    return FormalRepairRoute(UNCLASSIFIED)


def _counterexample(violation: Violation | None) -> str:
    if violation is None or violation.kind != "formal_counterexample":
        return ""
    if not isinstance(violation.evidence, dict):
        return ""
    issue = violation.evidence.get("issue", {})
    if not isinstance(issue, dict):
        return ""
    witness = issue.get("counterexample", "")
    return witness if isinstance(witness, str) else ""


def _ordered_pair_is_reversed(witness: str) -> bool:
    match = re.search(r"ordered_pair\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)", witness)
    return bool(match and int(match.group(1)) > int(match.group(2)))
