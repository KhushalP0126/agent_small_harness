from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_DEAL_TIMEOUT_SECONDS = 2.0


@dataclass
class DealContractIssue:
    function: str
    summary: str
    details: str


@dataclass
class DealContractResult:
    is_compliant: bool
    skipped: bool = False
    issues: list[DealContractIssue] = field(default_factory=list)
    checked_examples: int = 0


def is_deal_available() -> bool:
    return importlib.util.find_spec("deal") is not None


def serialize_deal_contract_result(result: DealContractResult) -> dict[str, Any]:
    return {
        "is_compliant": result.is_compliant,
        "skipped": result.skipped,
        "checked_examples": result.checked_examples,
        "issues": [asdict(issue) for issue in result.issues],
    }


def validate_deal_examples(
    source: str,
    timeout_seconds: float = DEFAULT_DEAL_TIMEOUT_SECONDS,
) -> DealContractResult:
    """Execute explicit Deal examples from generated code in a subprocess.

    This uses the real `deal` library. It intentionally checks only concrete
    `@deal.example` validators so the harness does not require Hypothesis for
    `deal.cases()`.
    """

    if "deal." not in source and "import deal" not in source:
        return DealContractResult(is_compliant=True, skipped=True)
    if not is_deal_available():
        return DealContractResult(is_compliant=True, skipped=True)

    with tempfile.TemporaryDirectory(prefix="agent_harness_deal_") as tmpdir:
        tmp = Path(tmpdir)
        candidate = tmp / "candidate.py"
        driver = tmp / "run_deal_examples.py"
        candidate.write_text(source, encoding="utf-8")
        driver.write_text(_driver_source(candidate), encoding="utf-8")
        try:
            completed = subprocess.run(
                [sys.executable, str(driver)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return DealContractResult(
                is_compliant=False,
                issues=[
                    DealContractIssue(
                        function="<module>",
                        summary="Deal example validation timed out",
                        details=str(exc),
                    )
                ],
            )

    output = completed.stdout.strip()
    if completed.returncode != 0:
        details = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
        return DealContractResult(
            is_compliant=False,
            issues=[
                DealContractIssue(
                    function="<module>",
                    summary="Deal example validation crashed",
                    details=details or f"deal validation exited with code {completed.returncode}",
                )
            ],
        )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        return DealContractResult(
            is_compliant=False,
            issues=[
                DealContractIssue(
                    function="<module>",
                    summary="Deal example validation produced invalid output",
                    details=str(exc),
                )
            ],
        )

    issues = [
        DealContractIssue(
            function=str(item.get("function", "<unknown>")),
            summary=str(item.get("summary", "Deal example failed")),
            details=str(item.get("details", "")),
        )
        for item in payload.get("issues", [])
    ]
    return DealContractResult(
        is_compliant=not issues,
        skipped=bool(payload.get("checked_examples", 0) == 0),
        issues=issues,
        checked_examples=int(payload.get("checked_examples", 0)),
    )


def _driver_source(candidate: Path) -> str:
    return f"""
from __future__ import annotations

import json
import runpy

namespace = runpy.run_path({str(candidate)!r})
issues = []
checked = 0

for name, value in namespace.items():
    contract = getattr(value, "__deal_contract", None)
    examples = list(getattr(contract, "examples", []) or [])
    for example in examples:
        checked += 1
        try:
            example.validate((), {{}})
        except Exception as exc:
            issues.append({{
                "function": name,
                "summary": "Deal example failed",
                "details": f"{{type(exc).__name__}}: {{exc}}",
            }})

print(json.dumps({{"checked_examples": checked, "issues": issues}}, sort_keys=True))
"""
