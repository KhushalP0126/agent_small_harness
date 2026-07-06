from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from agents.base import AgentResult, BaseAgent


FILLER_PATTERNS = (
    r"\bplease\b",
    r"\bhey\b",
    r"\bhi\b",
    r"\bhello\b",
    r"\bcan you\b",
    r"\bcould you\b",
    r"\bi want you to\b",
    r"\bi need you to\b",
    r"\bi want\b",
    r"\bi need\b",
    r"\bmaybe\b",
    r"\bactually\b",
    r"\bjust\b",
    r"\bkind of\b",
    r"\bsort of\b",
    r"\blike\b",
    r"\byou know\b",
)

COMMAND_REWRITES = (
    (re.compile(r"^\s*(build|create|make|write|implement|add|fix|refactor)\s+", re.IGNORECASE), r"\1 "),
)


@dataclass
class NormalizedPrompt:
    raw_prompt: str
    normalized_prompt: str
    removed_fragments: list[str] = field(default_factory=list)


class PromptNormalizerAgent(BaseAgent):
    """Turns conversational user input into a compact task prompt.

    This layer is intentionally deterministic. It removes common conversational filler
    before the small worker model sees the request, reducing token noise without
    giving another model a chance to reinterpret the task.
    """

    name = "agent-prompt-normalizer"

    def normalize(self, prompt: str) -> NormalizedPrompt:
        removed: list[str] = []
        normalized_lines: list[str] = []
        for raw_line in prompt.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = self._strip_markdown_list_prefix(line)
            line, removed_here = self._remove_fillers(line)
            removed.extend(removed_here)
            line = self._normalize_command(line)
            line = " ".join(line.split())
            if line:
                normalized_lines.append(line[0].upper() + line[1:])
        return NormalizedPrompt(
            raw_prompt=prompt,
            normalized_prompt="\n".join(normalized_lines),
            removed_fragments=removed,
        )

    def _strip_markdown_list_prefix(self, line: str) -> str:
        return re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", line)

    def _remove_fillers(self, line: str) -> tuple[str, list[str]]:
        removed: list[str] = []
        cleaned = line
        for pattern in FILLER_PATTERNS:
            matches = re.findall(pattern, cleaned, flags=re.IGNORECASE)
            removed.extend(match.lower() for match in matches)
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned.strip(" ,"), removed

    def _normalize_command(self, line: str) -> str:
        for pattern, replacement in COMMAND_REWRITES:
            line = pattern.sub(replacement, line)
        return line.strip()

    def run(self, prompt: str) -> AgentResult:
        result = self.normalize(prompt)
        return AgentResult(agent=self.name, payload=asdict(result))
