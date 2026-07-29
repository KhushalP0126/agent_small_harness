from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from engines.base import BaseEngine, EngineFinding


class CompilationEngine(BaseEngine):
    """Strict, timeout-bounded C/C++ syntax and warning gate."""

    name = "engine-compilation"

    def __init__(
        self,
        language: str,
        *,
        timeout_seconds: float = 10.0,
        compiler: str | None = None,
    ) -> None:
        normalized = language.strip().lower()
        if normalized not in {"c", "cpp"}:
            raise ValueError("CompilationEngine supports only c and cpp")
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
                    details=f"No C/C++ compiler is available for {self.language}.",
                    metrics={
                        "compile_status": "skipped",
                        "language": self.language,
                    },
                )
            ]

        suffix = ".c" if self.language == "c" else ".cpp"
        with tempfile.TemporaryDirectory(prefix="harness-compile-") as temp_dir:
            source_path = Path(temp_dir) / f"draft{suffix}"
            source_path.write_text(source, encoding="utf-8")
            command = [
                compiler,
                "-fsyntax-only",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(source_path),
            ]
            if self.language == "cpp":
                command.insert(1, "-std=c++17")
            else:
                command.insert(1, "-std=c11")
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
        candidates = (
            ("clang++", "g++")
            if self.language == "cpp"
            else ("clang", "gcc")
        )
        return next(
            (resolved for name in candidates if (resolved := shutil.which(name))),
            None,
        )
