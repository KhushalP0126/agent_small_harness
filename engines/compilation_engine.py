from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from engines.base import BaseEngine, EngineFinding


class CompilationEngine(BaseEngine):
    """Timeout-bounded syntax/build gate for compiled and interpreted drafts."""

    name = "engine-compilation"

    def __init__(
        self,
        language: str,
        *,
        timeout_seconds: float = 10.0,
        compiler: str | None = None,
    ) -> None:
        normalized = language.strip().lower()
        aliases = {"c++": "cpp", "cxx": "cpp", "rs": "rust", "js": "javascript"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"c", "cpp", "rust", "javascript"}:
            raise ValueError("CompilationEngine supports c, cpp, rust, and javascript")
        self.language = normalized
        self.timeout_seconds = timeout_seconds
        self.compiler = compiler

    def scan(self, source: str) -> list[EngineFinding]:
        compiler = self.compiler or self._find_compiler()
        if compiler is None:
            return [
                EngineFinding(
                    engine=self.name,
                    severity="Low",
                    summary="Compilation gate skipped",
                    details=f"No syntax/build tool is available for {self.language}.",
                    metrics={
                        "compile_status": "skipped",
                        "language": self.language,
                    },
                )
            ]

        suffix = {
            "c": ".c",
            "cpp": ".cpp",
            "rust": ".rs",
            "javascript": ".js",
        }[self.language]
        with tempfile.TemporaryDirectory(prefix="harness-compile-") as temp_dir:
            source_path = Path(temp_dir) / f"draft{suffix}"
            source_path.write_text(source, encoding="utf-8")
            command = self._command(compiler, source_path, Path(temp_dir))
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return [
                    EngineFinding(
                        engine=self.name,
                        severity="High",
                        summary="Compilation gate timed out",
                        details=(
                            f"{Path(compiler).name} exceeded "
                            f"{self.timeout_seconds:.1f}s."
                        ),
                        metrics={
                            "compile_status": "timeout",
                            "language": self.language,
                            "compiler": compiler,
                        },
                    )
                ]

        if completed.returncode == 0:
            return []
        errors = (completed.stderr or completed.stdout).strip()
        return [
            EngineFinding(
                engine=self.name,
                severity="High",
                summary="Draft failed strict compilation",
                details=errors[-4000:] or f"{Path(compiler).name} exited non-zero.",
                metrics={
                    "compile_status": "fail",
                    "language": self.language,
                    "compiler": compiler,
                    "returncode": completed.returncode,
                    "errors": errors[-4000:],
                },
            )
        ]

    def _find_compiler(self) -> str | None:
        candidates = {
            "c": ("clang", "gcc"),
            "cpp": ("clang++", "g++"),
            "rust": ("rustc",),
            "javascript": ("node",),
        }[self.language]
        return next(
            (resolved for name in candidates if (resolved := shutil.which(name))),
            None,
        )

    def _command(self, tool: str, source_path: Path, temp_dir: Path) -> list[str]:
        if self.language == "c":
            return [tool, "-std=c11", "-fsyntax-only", "-Wall", "-Wextra", "-Werror", str(source_path)]
        if self.language == "cpp":
            return [tool, "-std=c++17", "-fsyntax-only", "-Wall", "-Wextra", "-Werror", str(source_path)]
        if self.language == "rust":
            # A library crate lets contract fragments compile without forcing a
            # generated ``main`` function, while still type-checking the draft.
            return [
                tool,
                "--edition",
                "2021",
                "--crate-type",
                "lib",
                "--emit",
                "metadata",
                "-o",
                str(temp_dir / "draft.rmeta"),
                str(source_path),
            ]
        return [tool, "--check", str(source_path)]
