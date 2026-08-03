"""Typed build/run contracts for supported generated-code languages."""

from __future__ import annotations

from dataclasses import dataclass

from harness_kernel.tool_registry import ToolError


@dataclass(frozen=True)
class LanguageAdapter:
    language: str
    aliases: tuple[str, ...]
    filename: str
    container_image: str
    container_command: tuple[str, ...]
    local_command: tuple[str, ...]


ADAPTERS = (
    LanguageAdapter(
        "python",
        ("py", "python3"),
        "candidate.py",
        "python:3.11-slim",
        ("python3", "-I", "/workspace/candidate.py"),
        ("python3", "-I", "candidate.py"),
    ),
    LanguageAdapter(
        "c",
        ("c11",),
        "candidate.c",
        "gcc:14",
        ("sh", "-c", "gcc -std=c11 -Wall -Wextra -Werror candidate.c -o candidate && ./candidate"),
        ("sh", "-c", "cc -std=c11 -Wall -Wextra -Werror candidate.c -o candidate && ./candidate"),
    ),
    LanguageAdapter(
        "cpp",
        ("c++", "cxx"),
        "candidate.cpp",
        "gcc:14",
        ("sh", "-c", "g++ -std=c++20 -Wall -Wextra -Werror candidate.cpp -o candidate && ./candidate"),
        ("sh", "-c", "c++ -std=c++20 -Wall -Wextra -Werror candidate.cpp -o candidate && ./candidate"),
    ),
    LanguageAdapter(
        "rust",
        ("rs",),
        "candidate.rs",
        "rust:1.88-slim",
        ("sh", "-c", "rustc --edition=2024 candidate.rs -o candidate && ./candidate"),
        ("sh", "-c", "rustc --edition=2024 candidate.rs -o candidate && ./candidate"),
    ),
    LanguageAdapter(
        "javascript",
        ("js", "node", "nodejs"),
        "candidate.js",
        "node:22-slim",
        ("node", "/workspace/candidate.js"),
        ("node", "candidate.js"),
    ),
)

_BY_NAME = {
    name: adapter
    for adapter in ADAPTERS
    for name in (adapter.language, *adapter.aliases)
}


def get_language_adapter(language: str) -> LanguageAdapter:
    normalized = language.strip().casefold()
    adapter = _BY_NAME.get(normalized)
    if adapter is None:
        raise ToolError(
            f"Unsupported execution language {language!r}; expected one of: "
            + ", ".join(adapter.language for adapter in ADAPTERS),
            kind="unsupported_language",
        )
    return adapter


def supported_languages() -> tuple[str, ...]:
    return tuple(adapter.language for adapter in ADAPTERS)
