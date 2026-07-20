from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding
from engines.library_registry import LibraryRegistry


PYLINT_TIMEOUT_SECONDS = 8


class LintEngine(BaseEngine):
    name = "engine-5-lint"

    def __init__(
        self,
        executable: str | None = None,
        timeout_seconds: int = PYLINT_TIMEOUT_SECONDS,
        library_registry: LibraryRegistry | None = None,
    ) -> None:
        self.executable = executable if executable is not None else shutil.which("pylint")
        self.timeout_seconds = timeout_seconds
        self.library_registry = library_registry or LibraryRegistry()

    def scan(self, source: str) -> list[EngineFinding]:
        if not self.executable:
            return [
                EngineFinding(
                    engine=self.name,
                    severity="Low",
                    summary="Pylint unavailable",
                    details="Pylint is not installed; lint checks were skipped.",
                    metrics={"available": False, "lint_skipped": True, "lint_status": "unavailable"},
                )
            ]

        with tempfile.NamedTemporaryFile("w", suffix=".py", encoding="utf-8", delete=False) as handle:
            handle.write(source)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                [
                    self.executable,
                    "--disable=all",
                    "--enable=E,F",
                    "--output-format=json",
                    "--score=n",
                    "--reports=n",
                    str(temp_path),
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return [
                EngineFinding(
                    engine=self.name,
                    severity="Low",
                    summary="Pylint timeout",
                    details="Pylint exceeded the lint timeout; lint checks were skipped for this draft.",
                    metrics={
                        "available": True,
                        "lint_skipped": True,
                        "lint_status": "timeout",
                        "timeout_seconds": self.timeout_seconds,
                    },
                )
            ]
        finally:
            temp_path.unlink(missing_ok=True)

        if completed.stdout.strip():
            try:
                messages = json.loads(completed.stdout)
            except json.JSONDecodeError:
                return [
                    EngineFinding(
                        engine=self.name,
                        severity="Low",
                        summary="Pylint output parse failure",
                        details="Pylint returned non-JSON output; lint checks were skipped for this draft.",
                        metrics={
                            "available": True,
                            "lint_skipped": True,
                            "lint_status": "output_parse_failure",
                            "returncode": completed.returncode,
                            "stderr": completed.stderr.strip(),
                        },
                    )
                ]
        else:
            messages = []

        dynamic_member_messages = [
            message for message in messages if self._is_registered_dynamic_member(message)
        ]
        blocking_messages = [
            message
            for message in messages
            if str(message.get("type", "")).lower() in {"error", "fatal"}
            and message not in dynamic_member_messages
        ]
        warning_findings = [
            self._warning_for_registered_dynamic_member(message)
            for message in dynamic_member_messages
        ]
        if not blocking_messages:
            return warning_findings or [
                EngineFinding(
                    engine=self.name,
                    severity="Low",
                    summary="No blocking lint issues detected",
                    details="Pylint reported no fatal or error category messages.",
                    metrics={
                        "available": True,
                        "lint_skipped": False,
                        "lint_status": "completed",
                        "message_count": len(messages),
                    },
                )
            ]

        return [*warning_findings, *[self._finding_for_message(message) for message in blocking_messages]]

    def _is_registered_dynamic_member(self, message: dict) -> bool:
        if str(message.get("symbol", "")) != "no-member":
            return False
        text = str(message.get("message", ""))
        match = re.search(r"Module '([^']+)' has no '([^']+)' member", text)
        if not match:
            return False
        module_name, member_name = match.groups()
        schema = self.library_registry.get(module_name)
        return schema is not None and (
            member_name in schema.allowed_constants or member_name in schema.allowed_calls
        )

    def _warning_for_registered_dynamic_member(self, message: dict) -> EngineFinding:
        symbol = str(message.get("symbol", "no-member"))
        message_id = str(message.get("message-id", ""))
        line = int(message.get("line") or 0)
        column = int(message.get("column") or 0)
        text = str(message.get("message", "Pylint reported a registered dynamic member."))
        return EngineFinding(
            engine=self.name,
            severity="Warn",
            summary="Registered dynamic library member warning",
            details=text,
            metrics={
                "message_id": message_id,
                "symbol": symbol,
                "line": line,
                "column": column,
                "category": "warning",
                "blocking": False,
            },
            diagnostic=EngineDiagnostic(
                violation="",
                threshold="registered dynamic library members are advisory",
                actual=f"{message_id} {symbol}".strip(),
                location=f"line {line}:{column}",
                recommended_refactor="",
            ),
        )

    def _finding_for_message(self, message: dict) -> EngineFinding:
        category = str(message.get("type", "")).lower()
        symbol = str(message.get("symbol", "lint-error"))
        message_id = str(message.get("message-id", ""))
        line = int(message.get("line") or 0)
        column = int(message.get("column") or 0)
        text = str(message.get("message", "Pylint reported an error."))
        summary = "Pylint fatal" if category == "fatal" else "Pylint error"
        return EngineFinding(
            engine=self.name,
            severity="High",
            summary=summary,
            details=text,
            metrics={
                "message_id": message_id,
                "symbol": symbol,
                "line": line,
                "column": column,
                "category": category,
            },
            diagnostic=EngineDiagnostic(
                violation="LINT_ERROR",
                threshold="no Pylint fatal/error messages",
                actual=f"{message_id} {symbol}".strip(),
                location=f"line {line}:{column}",
                recommended_refactor=(
                    "Fix the reported undefined names, invalid imports, bad calls, or fatal lint errors before retrying."
                ),
            ),
        )
