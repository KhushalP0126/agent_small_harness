from __future__ import annotations


def build_backend_failure_architect_prompt(
    stage: str,
    backend: str,
    error: str,
    plan_packet: str,
    worker_model: str = "",
    contract_count: int = 0,
    contract_queue_summary: list[str] | None = None,
) -> str:
    """Build an architect prompt when the local worker fails before returning code."""

    summary = contract_queue_summary or []
    return "\n".join(
        [
            "SMALL WORKER BACKEND FAILURE",
            "",
            "The local worker failed before producing a draft, so the engines had no code to judge.",
            "",
            "FAILURE:",
            f"- Stage: {stage}",
            f"- Backend: {backend}",
            f"- Worker model: {worker_model or '(unknown)'}",
            f"- Error: {error}",
            f"- Prompt size: {len(plan_packet)} chars",
            f"- Contract count: {contract_count}",
            "",
            "CONTRACT QUEUE SUMMARY:",
            *(f"- {item}" for item in summary),
            *(["- (none)"] if not summary else []),
            "",
            "PLAN / WORKER PACKET:",
            plan_packet.strip(),
            "",
            "ARCHITECT TASK:",
            "- Return complete Python code directly, or simplify the requested implementation while preserving the plan.",
            "- The returned code will be parsed and scanned by all harness engines.",
            "- If Deal decorators are present, their examples must pass.",
            "- Do not return prose or markdown fences.",
            "",
            "Return code only.",
        ]
    )
