"""Bounded, advisory peer consultation for stalled code-repair workers.

The consultant receives a deliberately small evidence packet and returns advice
only. It never receives repository tools and its response is not executable.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


MAX_SOURCE_EXCERPT_CHARS = 3_200
MAX_MEMO_CHARS = 3_000
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT_SECRET = re.compile(
    r"(?im)\b(api[_-]?key|token|password|secret|credential)\b\s*[:=]\s*[^\s,;]+"
)


@dataclass(frozen=True)
class PeerConsultation:
    """Persistable advisory record; deliberately excludes the full prompt/history."""

    trigger: str
    attempt: int
    failure_signature: str
    memo: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def redact_sensitive_text(text: str) -> str:
    """Keep consultation packets and persisted memos free of obvious credentials."""

    text = _BEARER.sub("Bearer [REDACTED]", text)
    return _ASSIGNMENT_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)


def bounded_source_excerpt(source: str, limit: int = MAX_SOURCE_EXCERPT_CHARS) -> str:
    source = redact_sensitive_text(source)
    if len(source) <= limit:
        return source
    head = limit // 2
    tail = limit - head
    return f"{source[:head]}\n# ... excerpt truncated ...\n{source[-tail:]}"


def build_peer_consultation_prompt(
    *,
    target: str,
    attempt: int,
    failure_signature: str,
    violation: dict[str, Any],
    diagnostic_deltas: list[dict[str, Any]],
    source: str,
) -> str:
    """Create a compact, evidence-only request for a different line of thought."""

    lessons = []
    for delta in diagnostic_deltas[-2:]:
        current = str(delta.get("current", ""))[:240]
        previous = str(delta.get("previous", ""))[:240]
        lessons.append(f"- Previous: {previous or 'unknown'}; current: {current or 'unknown'}")
    return "\n".join(
        [
            "PEER CONSULTATION — ADVICE ONLY",
            "You are a fresh diagnostician. Do not return code, patches, commands, or a full solution.",
            "Give at most three materially different repair hypotheses. For each, state why it may work and what evidence to inspect.",
            "Avoid repeating the failed direction. Preserve the stated public contract and safety constraints.",
            "",
            f"TASK: {redact_sensitive_text(target)[:500]}",
            f"ATTEMPT: {attempt}",
            f"FAILURE SIGNATURE: {redact_sensitive_text(failure_signature)[:500]}",
            "CURRENT FAILURE:",
            f"- Kind: {redact_sensitive_text(str(violation.get('kind', 'unknown')))}",
            f"- Location: {redact_sensitive_text(str(violation.get('location', 'unknown')))}",
            f"- Diagnostic: {redact_sensitive_text(str(violation.get('rationale') or violation.get('summary') or 'unknown'))[:700]}",
            "PRIOR ATTEMPT LESSONS:",
            *(lessons or ["- No compact prior lesson is available."]),
            "RELEVANT DRAFT EXCERPT:",
            bounded_source_excerpt(source),
        ]
    )


def bounded_peer_memo(text: str, limit: int = MAX_MEMO_CHARS) -> str:
    """Normalize an untrusted consultant response before it reaches a worker or journal."""

    text = redact_sensitive_text(text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n[peer memo truncated]"
    return text


def is_advisory_memo(text: str) -> bool:
    """Reject a consultant response that tries to become an implementation worker."""

    prohibited_prefixes = (
        "def ",
        "class ",
        "import ",
        "from ",
        "return ",
        "function ",
        "patch ",
        "diff ",
    )
    lines = [line.strip().casefold() for line in text.splitlines() if line.strip()]
    return bool(lines) and not any(
        line.startswith(prohibited_prefixes) or line.startswith("```")
        for line in lines
    )
