from __future__ import annotations

from dataclasses import dataclass


DEFAULT_PROMPT_CHAR_BUDGET = 24000
CONTINUATION_TAIL_CHARS = 6000


@dataclass(frozen=True)
class PromptBudgetResult:
    text: str
    original_chars: int
    final_chars: int
    truncated: bool
    strategy: str = ""


def estimate_tokens(text: str) -> int:
    """Conservative local token estimate for telemetry and budget decisions."""

    return max(1, (len(text) + 3) // 4) if text else 0


def budget_prompt(text: str, max_chars: int = DEFAULT_PROMPT_CHAR_BUDGET) -> PromptBudgetResult:
    original_chars = len(text)
    if max_chars <= 0 or original_chars <= max_chars:
        return PromptBudgetResult(
            text=text,
            original_chars=original_chars,
            final_chars=original_chars,
            truncated=False,
        )
    marker = (
        "PROMPT BUDGET APPLIED: older context was removed; preserve the current draft, "
        "latest failures, and final rules.\n\n"
    )
    if max_chars <= len(marker):
        trimmed = marker[:max_chars]
        return PromptBudgetResult(
            text=trimmed,
            original_chars=original_chars,
            final_chars=len(trimmed),
            truncated=True,
            strategy="budget_marker_only",
        )
    tail_budget = max(0, max_chars - len(marker))
    trimmed = marker + text[-tail_budget:]
    return PromptBudgetResult(
        text=trimmed,
        original_chars=original_chars,
        final_chars=len(trimmed),
        truncated=True,
        strategy="tail_preserve_latest_context",
    )


def continuation_prompt(partial_response: str, tail_chars: int = CONTINUATION_TAIL_CHARS) -> str:
    tail = partial_response[-tail_chars:]
    return "\n".join(
        [
            "The previous response appears truncated.",
            "Continue from exactly the tail below and return only the missing continuation.",
            "Do not restart the file and do not include prose.",
            "",
            "PARTIAL RESPONSE TAIL:",
            tail,
        ]
    )


def looks_truncated_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.startswith("```") and not stripped.endswith("```"):
        return True
    return _unbalanced_pairs(stripped)


def _unbalanced_pairs(text: str) -> bool:
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single or in_double:
            continue
        if char in pairs:
            stack.append(pairs[char])
        elif char in pairs.values():
            if not stack or stack.pop() != char:
                return True
    return bool(stack)
