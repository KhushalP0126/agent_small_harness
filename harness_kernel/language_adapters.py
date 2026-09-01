"""Typed build/run contracts for supported generated-code languages."""

from __future__ import annotations

from dataclasses import dataclass

from harness_kernel.tool_registry import ToolError


@dataclass(frozen=True)
class LanguageProfile:
    language: str
    aliases: tuple[str, ...]
    filename: str
    container_image: str
    container_command: tuple[str, ...]
    local_command: tuple[str, ...]
    suffixes: tuple[str, ...] = ()
    project_markers: tuple[str, ...] = ()
    build_command: tuple[str, ...] = ()
    test_command: tuple[str, ...] = ()
    lint_command: tuple[str, ...] = ()
    lockfiles: tuple[str, ...] = ()
    parser: str = "tree-sitter"
    capabilities: frozenset[str] = frozenset({"parse", "build", "test", "lint", "imports"})


# Backwards-compatible name for callers that still execute a single source file.
LanguageAdapter = LanguageProfile


ADAPTERS = (
    LanguageProfile(
        "python",
        ("py", "python3"),
        "candidate.py",
        "python:3.11-slim",
        ("python3", "-I", "/workspace/candidate.py"),
        ("python3", "-I", "candidate.py"),
        (".py",), ("pyproject.toml",), ("python3", "-m", "compileall", "-q", "."),
        ("python3", "-m", "pytest", "-q"), ("python3", "-m", "pylint", "--errors-only", "."),
        ("poetry.lock", "uv.lock", "requirements.lock"), "python-ast",
    ),
    LanguageProfile(
        "c",
        ("c11",),
        "candidate.c",
        "gcc:14",
        ("sh", "-c", "gcc -std=c11 -Wall -Wextra -Werror candidate.c -o candidate && ./candidate"),
        ("sh", "-c", "cc -std=c11 -Wall -Wextra -Werror candidate.c -o candidate && ./candidate"),
        (".c", ".h"), ("CMakeLists.txt",), ("cmake", "--build", "build"),
        ("ctest", "--test-dir", "build", "--output-on-failure"), (), (),
    ),
    LanguageProfile(
        "cpp",
        ("c++", "cxx"),
        "candidate.cpp",
        "gcc:14",
        ("sh", "-c", "g++ -std=c++20 -Wall -Wextra -Werror candidate.cpp -o candidate && ./candidate"),
        ("sh", "-c", "c++ -std=c++20 -Wall -Wextra -Werror candidate.cpp -o candidate && ./candidate"),
        (".cc", ".cpp", ".cxx", ".hpp", ".h"), ("CMakeLists.txt",), ("cmake", "--build", "build"),
        ("ctest", "--test-dir", "build", "--output-on-failure"), (), (),
    ),
    LanguageProfile(
        "rust",
        ("rs",),
        "candidate.rs",
        "rust:1.88-slim",
        ("sh", "-c", "rustc --edition=2024 candidate.rs -o candidate && ./candidate"),
        ("sh", "-c", "rustc --edition=2024 candidate.rs -o candidate && ./candidate"),
        (".rs",), ("Cargo.toml",), ("cargo", "check", "--locked"),
        ("cargo", "test", "--locked"), ("cargo", "clippy", "--locked", "--", "-D", "warnings"),
        ("Cargo.lock",),
    ),
    LanguageProfile(
        "javascript",
        ("js", "node", "nodejs"),
        "candidate.js",
        "node:22-slim",
        ("node", "/workspace/candidate.js"),
        ("node", "candidate.js"),
        (".js", ".mjs", ".cjs"), ("package.json",), ("node", "--check"),
        ("npm", "test"), ("npm", "run", "lint", "--if-present"),
        ("package-lock.json", "npm-shrinkwrap.json"),
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


def get_language_profile(language: str) -> LanguageProfile:
    return get_language_adapter(language)


def detect_project(root: "Path") -> LanguageProfile | None:
    """Detect a canonical project deterministically by registry order."""
    from pathlib import Path
    root = Path(root)
    for profile in ADAPTERS:
        if any((root / marker).is_file() for marker in profile.project_markers):
            return profile
    return None
