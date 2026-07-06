from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_FORMAL_TIMEOUT_SECONDS = 3.0


@dataclass
class FormalIssue:
    tool: str
    summary: str
    details: str


@dataclass
class FormalResult:
    is_compliant: bool
    skipped: bool = False
    tool: str = "crosshair"
    issues: list[FormalIssue] = field(default_factory=list)


def is_crosshair_available() -> bool:
    return importlib.util.find_spec("crosshair") is not None


def serialize_formal_result(result: FormalResult) -> dict[str, Any]:
    return {
        "is_compliant": result.is_compliant,
        "skipped": result.skipped,
        "tool": result.tool,
        "issues": [asdict(issue) for issue in result.issues],
    }


def validate_with_crosshair(
    source: str,
    timeout_seconds: float = DEFAULT_FORMAL_TIMEOUT_SECONDS,
) -> FormalResult:
    """Run CrossHair when available and treat absence as a non-blocking skip.

    CrossHair is a semantic verifier, not a style engine. It only has useful
    work when the source contains type hints, asserts, icontract/deal contracts,
    or other checkable conditions. The harness keeps it optional so generated
    code can still be tested on machines without the dependency installed.
    """

    if not is_crosshair_available():
        return FormalResult(is_compliant=True, skipped=True)

    with tempfile.TemporaryDirectory(prefix="agent_harness_crosshair_") as tmpdir:
        path = Path(tmpdir) / "candidate.py"
        path.write_text(source, encoding="utf-8")
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "crosshair",
                    "check",
                    str(path),
                    "--per_condition_timeout",
                    str(max(timeout_seconds, 0.1)),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 1.0,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return FormalResult(
                is_compliant=False,
                issues=[
                    FormalIssue(
                        tool="crosshair",
                        summary="CrossHair timed out",
                        details=str(exc),
                    )
                ],
            )

    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if part and part.strip()
    )
    if completed.returncode == 0:
        return FormalResult(is_compliant=True)
    return FormalResult(
        is_compliant=False,
        issues=[
            FormalIssue(
                tool="crosshair",
                summary="CrossHair found a contract or assertion issue",
                details=output or f"crosshair exited with code {completed.returncode}",
            )
        ],
    )
