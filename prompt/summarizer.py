"""Deterministic compression for older retry-attempt history."""

from __future__ import annotations

import re
from dataclasses import dataclass


ATTEMPT_RE = re.compile(r"^-\s+Attempt\s+(?P<number>\d+):\s*$")
SIGNAL_PREFIXES = (
    "Static failure:",
    "Behavior failure:",
    "Formal failure:",
)


@dataclass(frozen=True)
class DefaultPromptSummarizer:
    """Collapse verbose prior attempts without rewriting live diagnostics."""

    max_attempts: int = 8
    max_chars: int = 4000

    def __call__(self, text: str) -> str:
        attempts = self._attempt_summaries(text)
        if attempts:
            omitted = max(0, len(attempts) - self.max_attempts)
            selected = attempts[-self.max_attempts :]
            lines = ["Prior-attempt summary:"]
            if omitted:
                lines.append(f"- {omitted} older attempt(s) omitted.")
            lines.extend(f"- {line}" for line in selected)
            lines.append("- Do not repeat these failed patterns.")
            return self._bounded("\n".join(lines))

        signals = []
        for raw_line in text.splitlines():
            line = " ".join(raw_line.strip().split())
            if not line:
                continue
            lowered = line.lower()
            if any(
                token in lowered
                for token in (
                    "failure",
                    "violation",
                    "required",
                    "expected",
                    "attempt",
                    "diagnostic",
                )
            ) and line not in signals:
                signals.append(line)
        if not signals:
            signals = [" ".join(text.split())]
        return self._bounded("\n".join(f"- {line}" for line in signals[: self.max_attempts]))

    def _attempt_summaries(self, text: str) -> list[str]:
        attempts: list[str] = []
        current_number: str | None = None
        current_signals: list[str] = []
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            match = ATTEMPT_RE.match(stripped)
            if match:
                self._append_attempt(attempts, current_number, current_signals)
                current_number = match.group("number")
                current_signals = []
                continue
            if current_number is None:
                continue
            if stripped.startswith(SIGNAL_PREFIXES):
                compact = " ".join(stripped.split())
                if compact not in current_signals:
                    current_signals.append(compact)
        self._append_attempt(attempts, current_number, current_signals)
        return attempts

    @staticmethod
    def _append_attempt(
        attempts: list[str],
        number: str | None,
        signals: list[str],
    ) -> None:
        if number is None:
            return
        detail = "; ".join(signals) if signals else "failed without structured details"
        attempts.append(f"Attempt {number}: {detail}")

    def _bounded(self, text: str) -> str:
        if len(text) <= self.max_chars:
            return text
        marker = "\n- Summary truncated."
        return text[: max(0, self.max_chars - len(marker))].rstrip() + marker
