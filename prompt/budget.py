from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


DEFAULT_PROMPT_CHAR_BUDGET = 24000
CONTINUATION_TAIL_CHARS = 6000
PromptSummarizer = Callable[[str], str]


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


def budget_prompt(
    text: str,
    max_chars: int = DEFAULT_PROMPT_CHAR_BUDGET,
    summarizer: PromptSummarizer | None = None,
) -> PromptBudgetResult:
    original_chars = len(text)
    if max_chars <= 0 or original_chars <= max_chars:
        return PromptBudgetResult(
            text=text,
            original_chars=original_chars,
            final_chars=original_chars,
            truncated=False,
        )
    if summarizer is not None:
        summarized_history = _summarize_prior_attempt_history(
            text,
            max_chars,
            summarizer,
        )
        if summarized_history is not None:
            return PromptBudgetResult(
                text=summarized_history,
                original_chars=original_chars,
                final_chars=len(summarized_history),
                truncated=True,
                strategy="summarize_prior_attempts_preserve_diagnostics",
            )
        summarized = _summarize_older_context(text, max_chars, summarizer)
        if summarized is not None:
            return PromptBudgetResult(
                text=summarized,
                original_chars=original_chars,
                final_chars=len(summarized),
                truncated=True,
                strategy="summarize_older_preserve_latest",
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


def _summarize_prior_attempt_history(
    text: str,
    max_chars: int,
    summarizer: PromptSummarizer,
) -> str | None:
    header = "PRIOR FAILED ATTEMPTS:"
    start = text.find(header)
    if start < 0:
        return None
    body_start = start + len(header)
    end_markers = (
        "\n\nDIAGNOSTIC DELTAS:",
        "\n\nARCHITECT MODE:",
        "\n\nTARGETED REPAIR INSTRUCTIONS:",
        "\n\nDEBUGGER OBSERVATIONS",
    )
    ends = [
        position
        for marker in end_markers
        if (position := text.find(marker, body_start)) >= 0
    ]
    body_end = min(ends, default=len(text))
    try:
        summary = summarizer(text[body_start:body_end]).strip()
    except Exception:
        return None
    if not summary:
        return None
    candidate = (
        text[:body_start]
        + "\n"
        + summary
        + text[body_end:]
    )
    if len(candidate) > max_chars:
        return None
    return candidate


def _summarize_older_context(
    text: str,
    max_chars: int,
    summarizer: PromptSummarizer,
) -> str | None:
    marker = "PROMPT BUDGET APPLIED: older context was summarized.\n\n"
    summary_header = "OLDER CONTEXT SUMMARY:\n"
    latest_header = "\n\nLATEST CONTEXT (verbatim):\n"
    fixed_chars = len(marker) + len(summary_header) + len(latest_header)
    if max_chars <= fixed_chars + 2:
        return None

    content_budget = max_chars - fixed_chars
    latest_budget = max(1, content_budget // 2)
    older = text[:-latest_budget]
    latest = text[-latest_budget:]
    if not older:
        return None
    try:
        summary = summarizer(older).strip()
    except Exception:
        return None
    if not summary:
        return None

    summary_budget = max(1, content_budget - len(latest))
    if len(summary) > summary_budget:
        summary = summary[:summary_budget]
    result = f"{marker}{summary_header}{summary}{latest_header}{latest}"
    return result[:max_chars]


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
