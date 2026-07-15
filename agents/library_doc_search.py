from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Callable


GenerateText = Callable[[str], str]
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class DocumentationSearchResult:
    provider: str
    model: str
    searched_by_model: bool
    query: str
    documentation: list[dict]
    raw_response: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class LibraryDocumentationSearchAgent:
    """Asks a configured model to find library documentation candidates."""

    name = "agent-library-documentation-search"

    def __init__(
        self,
        provider: str,
        model: str,
        generate_text: GenerateText,
    ) -> None:
        self.provider = provider
        self.model = model
        self.generate_text = generate_text

    def search(self, library: str, public_symbols: list[str] | None = None) -> DocumentationSearchResult:
        symbols = ", ".join((public_symbols or [])[:20])
        query = f"{library} official documentation"
        prompt = "\n".join(
            [
                "Find the best official or primary documentation pages for this Python library.",
                "Prefer official project docs, source repository docs, API references, and package metadata.",
                "Return JSON only. Do not include markdown.",
                "",
                "JSON schema:",
                "{",
                '  "documentation": [',
                '    {"title": "string", "url": "https://...", "note": "one sentence"}',
                "  ]",
                "}",
                "",
                f"Library: {library}",
                f"Known public symbols: {symbols or 'none'}",
            ]
        )
        try:
            raw_response = self.generate_text(prompt)
            documentation = _parse_documentation(raw_response)
            return DocumentationSearchResult(
                provider=self.provider,
                model=self.model,
                searched_by_model=True,
                query=query,
                documentation=documentation,
                raw_response=raw_response,
            )
        except Exception as exc:  # noqa: BLE001 - persisted as reviewable discovery metadata
            return DocumentationSearchResult(
                provider=self.provider,
                model=self.model,
                searched_by_model=True,
                query=query,
                documentation=[],
                error=f"{exc.__class__.__name__}: {exc}",
            )

    def syntax_notes(
        self,
        library: str,
        public_symbols: list[str] | None = None,
        documentation: list[dict] | None = None,
    ) -> str:
        symbols = ", ".join((public_symbols or [])[:40])
        docs = "\n".join(
            f"- {item.get('title', '')}: {item.get('url', '')} ({item.get('note', '')})"
            for item in documentation or []
        )
        prompt = "\n".join(
            [
                "Write a concise Markdown syntax and usage guide for this Python library.",
                "Use the documentation candidates and public symbols below.",
                "Include setup, common imports, basic usage syntax, important objects, common methods, pitfalls, and a recommended trusted API surface.",
                "Return Markdown only. Do not wrap it in a code fence.",
                "",
                f"Library: {library}",
                f"Model provider: {self.provider}",
                f"Model name: {self.model}",
                f"Known public symbols: {symbols or 'none'}",
                "",
                "Documentation candidates:",
                docs or "- none",
            ]
        )
        response = self.generate_text(prompt)
        return _markdown_payload(response)


def _parse_documentation(response: str) -> list[dict]:
    payload = _json_payload(response)
    parsed = json.loads(payload)
    docs = parsed.get("documentation", []) if isinstance(parsed, dict) else parsed
    if not isinstance(docs, list):
        raise ValueError("model response did not contain a documentation list")
    normalized: list[dict] = []
    for item in docs:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        note = str(item.get("note", "")).strip()
        if not title or not url:
            continue
        normalized.append({"title": title, "url": url, "note": note})
    if not normalized:
        raise ValueError("model response did not include usable documentation entries")
    return normalized


def _json_payload(response: str) -> str:
    text = response.strip()
    match = JSON_BLOCK_RE.search(text)
    if match:
        text = match.group(1).strip()
    start = min((index for index in [text.find("{"), text.find("[")] if index >= 0), default=-1)
    if start > 0:
        text = text[start:]
    return text


def _markdown_payload(response: str) -> str:
    text = response.strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if match:
        text = match.group(1).strip()
    return text.rstrip() + "\n"
