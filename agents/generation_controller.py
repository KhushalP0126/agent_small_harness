from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import unified_diff
from typing import Callable

from agents.base import AgentResult, BaseAgent
from agents.engine_registry import EngineRegistry
from agents.parse_contract import ParseContractAgent, ParseFailure
from agents.repair_strategy import MANUAL_REVIEW, RepairStrategyAgent
from engines.base import EngineFinding
from prompt.architect_builder import build_state_machine_architect_prompt
from prompt.backend_failure_builder import build_backend_failure_architect_prompt
from prompt.budget import budget_prompt
from prompt.retry_builder import build_retry_prompt, build_small_worker_retry_prompt
from validation.behavior import FunctionBehaviorSpec, serialize_behavior_result, validate_function_behavior
from validation.branch_loop_detector import build_branch_state_signature, detect_branching_loop
from validation.deal_contracts import serialize_deal_contract_result, validate_deal_examples
from validation.finding_aggregator import aggregate_violations, serialize_repair_directives
from validation.formal import serialize_formal_result, validate_with_crosshair
from validation.policy import serialize_validation_result, validate_findings
from validation.types import ValidationResult, Violation


DraftSupplier = Callable[[str], str]
RepairSupplier = Callable[[str, str], str]

SEVERITY_RANK = {
    "info": 0,
    "low": 1,
    "warn": 2,
    "medium": 2,
    "block": 3,
    "high": 3,
}


@dataclass
class GenerationAttempt:
    attempt: int
    draft: str
    findings: list[dict]
    validation: dict
    behavior_validation: dict
    formal_validation: dict = field(default_factory=dict)
    diagnostic_deltas: list[dict] = field(default_factory=list)
    repair_directives: list[dict] = field(default_factory=list)
    retry_prompt: str = ""
    repair_worker: str = ""
    repair_error: str = ""
    draft_source_worker: str = ""
    changed: bool = True
    diff: str = ""
    branch_state_signature: dict = field(default_factory=dict)
    branch_loop: dict = field(default_factory=dict)
    backend_failure: dict = field(default_factory=dict)
    diagnostic_stagnant: bool = False


@dataclass
class BackendFailurePayload:
    backend: str
    stage: str
    reason: str
    error: str
    worker_model: str = ""
    prompt_size: int = 0
    contract_count: int = 0
    plan_packet: str = ""
    contract_queue_summary: list[str] = field(default_factory=list)


@dataclass
class HumanReviewPayload:
    status: str
    reason: str
    blocking_findings: list[dict]
    blocking_violations: list[dict]
    behavior_issues: list[dict]
    formal_issues: list[dict] = field(default_factory=list)
    last_retry_prompt: str = ""
    diagnostic_deltas: list[dict] = field(default_factory=list)
    repair_directives: list[dict] = field(default_factory=list)
    suggested_human_decision: str = ""


@dataclass
class GenerationSession:
    target: str
    route: str
    max_retries: int
    attempts: list[GenerationAttempt] = field(default_factory=list)
    final_status: str = "manual_review_required"
    human_review: HumanReviewPayload | None = None


class GenerationController(BaseAgent):
    name = "agent-generation-controller"

    def __init__(
        self,
        max_retries: int = 2,
        draft_supplier: DraftSupplier | None = None,
        repair_supplier: RepairSupplier | None = None,
        architect_supplier: RepairSupplier | None = None,
        architect_after_repair_attempts: int | None = None,
        policy: dict | None = None,
        behavior_spec: FunctionBehaviorSpec | None = None,
        behavior_timeout_seconds: float | None = None,
        crosshair_enabled: bool = False,
        crosshair_timeout_seconds: float = 3.0,
        debug: bool = False,
        engine_registry: EngineRegistry | None = None,
        parse_contract: ParseContractAgent | None = None,
        repair_strategy: RepairStrategyAgent | None = None,
        language: str | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.draft_supplier = draft_supplier or (lambda prompt: prompt)
        self.repair_supplier = repair_supplier or (lambda draft, retry_prompt: draft)
        self.architect_supplier = architect_supplier
        self.architect_after_repair_attempts = architect_after_repair_attempts
        self.policy = policy
        self.behavior_spec = behavior_spec
        self.behavior_timeout_seconds = behavior_timeout_seconds
        self.crosshair_enabled = crosshair_enabled
        self.crosshair_timeout_seconds = crosshair_timeout_seconds
        self.debug = debug
        self.engine_registry = engine_registry or EngineRegistry.default()
        self.parse_contract = parse_contract or ParseContractAgent()
        self.repair_strategy = repair_strategy
        self.language = language

    def _scan(self, source: str) -> list[EngineFinding]:
        parse_result = self.parse_contract.parse(source, language=self.language)
        if isinstance(parse_result, ParseFailure):
            return [parse_result.finding]
        if not self.engine_registry.has_language(parse_result.language):
            return [
                EngineFinding(
                    engine="engine-parse-contract",
                    severity="High",
                    summary="Unsupported language",
                    details=(
                        f"No engine set is registered for language '{parse_result.language}'. "
                        "Refusing to mark unanalyzed code as compliant."
                    ),
                    metrics={
                        "line": 0,
                        "offset": 0,
                        "error": "unsupported_language",
                        "language": parse_result.language,
                    },
                )
            ]
        return self.engine_registry.findings_for(source, parse_result.language)

    def _route(self, findings: list[EngineFinding]) -> str:
        complexity = next(
            (
                finding.metrics.get("cyclomatic_complexity", 0)
                for finding in findings
                if finding.engine == "engine-3-branching"
            ),
            0,
        )
        if complexity < 5:
            return "one_pass"
        if complexity < 10:
            return "iterative_retry"
        return "decompose_and_generate"

    def _diff_text(self, previous_draft: str, current_draft: str) -> str:
        if previous_draft == current_draft:
            return ""
        return "\n".join(
            unified_diff(
                previous_draft.splitlines(),
                current_draft.splitlines(),
                fromfile="attempt_prev",
                tofile="attempt_curr",
                lineterm="",
            )
        )

    def _is_stagnant(self, previous_draft: str, current_draft: str) -> bool:
        return previous_draft == current_draft

    def _is_diagnostic_stagnant(self, diagnostic_deltas: list[dict]) -> bool:
        meaningful = [delta for delta in diagnostic_deltas if delta.get("repeated")]
        if not meaningful:
            return False
        return all(not delta.get("improved", False) for delta in meaningful)

    def _debug_print(self, message: str) -> None:
        if self.debug:
            print(f"[Buddy] {message}")

    def _repair_worker_for(self, repair_attempt_index: int) -> tuple[str, RepairSupplier]:
        if (
            self.architect_supplier is not None
            and self.architect_after_repair_attempts is not None
            and repair_attempt_index >= self.architect_after_repair_attempts
        ):
            return "architect_llm", self.architect_supplier
        return "small_worker", self.repair_supplier

    def _behavior_violations(self, result: dict) -> list[Violation]:
        violations: list[Violation] = []
        for issue in result["issues"]:
            violations.append(
                Violation(
                    kind="behavior_mismatch",
                    engine="behavior-validator",
                    severity="High",
                    summary="Failed behavioral output spec",
                    rationale=issue["details"],
                    current_value=f"{issue['case']} returned {issue['actual']}",
                    allowed_value=issue["expected"],
                    repair_hint="preserve_behavior",
                    evidence={"case": issue},
                )
            )
        return violations

    def _formal_violations(self, result: dict) -> list[Violation]:
        violations: list[Violation] = []
        for issue in result.get("issues", []):
            tool = result.get("tool", "formal")
            violations.append(
                Violation(
                    kind="formal_counterexample",
                    engine=f"formal-{tool}",
                    severity="High",
                    summary=issue.get("summary", "Formal validation failed"),
                    rationale=issue.get("details", ""),
                    current_value="contract or assertion violation",
                    allowed_value="all checkable contracts and assertions hold",
                    repair_hint="satisfy_contract",
                    evidence={"issue": issue},
                )
            )
        return violations

    def _retry_violations(
        self,
        validation_result: ValidationResult,
        behavior_validation: dict,
        formal_validation: dict,
    ) -> list[Violation]:
        """Return every distinct failing gate in stable validation order."""

        candidates: list[Violation] = []
        if not validation_result.is_compliant:
            candidates.extend(validation_result.violations)
        if not behavior_validation.get("is_compliant", False):
            candidates.extend(self._behavior_violations(behavior_validation))
        if not formal_validation.get("is_compliant", False):
            candidates.extend(self._formal_violations(formal_validation))

        violations: list[Violation] = []
        seen: set[tuple[str, str, str, str, str, str]] = set()
        for violation in candidates:
            identity = (
                violation.engine,
                violation.kind,
                violation.location,
                violation.summary,
                violation.current_value,
                violation.allowed_value,
            )
            if identity in seen:
                continue
            seen.add(identity)
            violations.append(violation)
        return violations

    def _validate_formal_contracts(self, source: str) -> dict:
        deal_result = validate_deal_examples(source, timeout_seconds=self.crosshair_timeout_seconds)
        if not deal_result.is_compliant:
            result = serialize_deal_contract_result(deal_result)
            result["tool"] = "deal"
            return result
        if not deal_result.skipped:
            result = serialize_deal_contract_result(deal_result)
            result["tool"] = "deal"
            return result
        if self.crosshair_enabled:
            return serialize_formal_result(
                validate_with_crosshair(
                    source,
                    timeout_seconds=self.crosshair_timeout_seconds,
                )
            )
        return {
            "is_compliant": True,
            "skipped": True,
            "tool": "formal",
            "issues": [],
        }

    def _violation_key(self, violation: Violation | dict) -> str:
        if isinstance(violation, dict):
            return ":".join(
                [
                    str(violation.get("engine", "")),
                    str(violation.get("kind", "")),
                    str(violation.get("repair_hint", "")),
                ]
            )
        return ":".join([violation.engine, violation.kind, str(violation.repair_hint)])

    def _numeric_value(self, value: object) -> float | None:
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _diagnostic_deltas(
        self,
        previous_attempt: GenerationAttempt | None,
        current_violations: list[Violation],
    ) -> list[dict]:
        if previous_attempt is None:
            return []
        prior_by_key = {
            self._violation_key(violation): violation
            for violation in previous_attempt.validation.get("violations", [])
        }
        deltas: list[dict] = []
        for violation in current_violations:
            prior = prior_by_key.get(self._violation_key(violation))
            if prior is None:
                continue
            prior_actual = prior.get("current_value", "")
            current_actual = violation.current_value
            prior_number = self._numeric_value(prior_actual)
            current_number = self._numeric_value(current_actual)
            numeric_delta = (
                None if prior_number is None or current_number is None else current_number - prior_number
            )
            diagnostic = violation.evidence.get("diagnostic", {})
            deltas.append(
                {
                    "kind": violation.kind,
                    "engine": violation.engine,
                    "prior_actual": prior_actual,
                    "current_actual": current_actual,
                    "delta": numeric_delta,
                    "improved": numeric_delta is not None and numeric_delta < 0,
                    "repeated": True,
                    "location": violation.location,
                    "recommended_refactor": diagnostic.get("recommended_refactor", ""),
                }
            )
        return deltas

    def _delta_context(self, deltas: list[dict]) -> str:
        if not deltas:
            return ""
        lines = ["DIAGNOSTIC DELTAS:"]
        for delta in deltas:
            if delta["delta"] is None:
                change = "non-numeric comparison"
            elif delta["delta"] < 0:
                change = f"improved by {abs(delta['delta']):g}"
            elif delta["delta"] > 0:
                change = f"worsened by {delta['delta']:g}"
            else:
                change = "no improvement"
            lines.append(
                f"- {delta['kind']}: prior {delta['prior_actual']} -> current {delta['current_actual']} ({change})."
            )
            if delta.get("recommended_refactor"):
                lines.append(f"  Required next move: {delta['recommended_refactor']}")
        lines.append("A repeated violation with no improvement requires a stronger structural rewrite.")
        return "\n".join(lines)

    def _top_violation(self, violations: list[Violation]) -> Violation | None:
        if not violations:
            return None
        return sorted(
            violations,
            key=lambda violation: (
                -SEVERITY_RANK.get(violation.severity.lower(), 0),
                violation.engine,
                violation.kind,
            ),
        )[0]

    def _prompt_scope(
        self,
        source: str,
        violations: list[Violation],
        diagnostic_deltas: list[dict],
        worker_name: str,
    ) -> tuple[list[Violation], list]:
        # Retry violations are already deduplicated and ordered by validation
        # stage. Keep the complete set for both workers so a static failure
        # cannot hide a simultaneous behavioral or formal failure.
        scoped_violations = violations
        return (
            scoped_violations,
            aggregate_violations(
                source,
                scoped_violations,
                diagnostic_deltas=diagnostic_deltas,
            ),
        )

    def _preserved_plan_context(self, initial_prompt: str) -> str:
        """Extract durable Plan Mode context that repair prompts must preserve."""

        wanted_headers = {"EXAMPLES:", "STATE RULES:", "DEPENDENCY GRAPH:"}
        stop_headers = {
            "PLAN PACKET:",
            "FUNCTION:",
            "LANGUAGE:",
            "EXAMPLES:",
            "STATE RULES:",
            "DEPENDENCY GRAPH:",
            "ADAPTER RULES:",
            "PERFORMANCE RULES:",
            "SAFETY RULES:",
            "FINAL RULES:",
            "Contract examples for the worker:",
        }
        captured: list[str] = []
        active = False
        for raw_line in initial_prompt.splitlines():
            line = raw_line.strip()
            if not line:
                if active:
                    captured.append("")
                continue
            if line in wanted_headers:
                active = True
                captured.append(line)
                continue
            if line in stop_headers:
                active = False
                continue
            if active:
                captured.append(line)
        while captured and not captured[-1]:
            captured.pop()
        if not captured:
            return ""
        return "\n".join(
            [
                "Simplify syntax or control flow only if the following task graph and examples remain true.",
                *captured,
                "DO NOT BREAK:",
                "- preserve dependency, order, and state rules implied by this context",
                "- preserve every listed behavior example",
                "- do not simplify the code in a way that changes the preserved semantics",
            ]
        )

    def _is_state_machine_failure(
        self,
        violations: list[Violation],
        preserved_context: str,
    ) -> bool:
        """Return whether the section-parser-specific architect prompt applies."""

        kinds = {violation.kind for violation in violations}
        context = preserved_context.lower()
        has_section_state = "state rules:" in context and any(
            marker in context
            for marker in ("active section", "active_section", "section state")
        )
        has_key_value_flow = any(
            marker in context
            for marker in ("key/value", "key=value", "equals sign", "nested dict")
        )
        return (
            has_section_state
            and has_key_value_flow
            and bool(
                kinds
                & {
                    "state_flow_risk",
                    "behavior_mismatch",
                    "cyclomatic_complexity",
                    "loop_depth",
                }
            )
        )

    def _is_metric_scope_ambiguous(
        self,
        validation_result: ValidationResult,
        behavior_validation: dict,
        formal_validation: dict,
    ) -> bool:
        if not behavior_validation.get("is_compliant", False):
            return False
        if not formal_validation.get("is_compliant", False):
            return False
        violations = validation_result.violations
        if not violations:
            return False
        return all(violation.kind == "cyclomatic_complexity" for violation in violations)

    def _build_scoped_retry_prompt(
        self,
        source: str,
        violations: list[Violation],
        diagnostic_deltas: list[dict],
        worker_name: str,
        initial_prompt: str,
        failed_attempts: list[GenerationAttempt],
    ) -> tuple[str, list, list[Violation]]:
        scoped_violations, scoped_directives = self._prompt_scope(
            source,
            violations,
            diagnostic_deltas,
            worker_name,
        )
        if worker_name == "small_worker":
            retry_prompt = build_small_worker_retry_prompt(
                source,
                scoped_violations,
                preserve_context=self._preserved_plan_context(initial_prompt),
            )
            if failed_attempts:
                retry_prompt = (
                    f"{retry_prompt}\n\n"
                    "PREVIOUS FAILURE:\n"
                    "- A prior repair attempt did not pass validation. Do not repeat the same output."
                )
            if diagnostic_deltas:
                retry_prompt = f"{retry_prompt}\n\nPREVIOUS REPAIR SIGNAL:"
                for delta in diagnostic_deltas:
                    if delta.get("delta") == 0:
                        change = "no improvement"
                    elif delta.get("improved"):
                        change = "improved but still failing"
                    else:
                        change = "changed but still failing"
                    retry_prompt = (
                        f"{retry_prompt}\n"
                        f"- {delta.get('kind')}: {delta.get('prior_actual')} -> {delta.get('current_actual')} ({change})"
                    )
        else:
            preserved_context = self._preserved_plan_context(initial_prompt)
            if preserved_context and self._is_state_machine_failure(
                scoped_violations,
                preserved_context,
            ):
                retry_prompt = build_state_machine_architect_prompt(
                    current_code=source,
                    violations=scoped_violations,
                    preserved_context=preserved_context,
                )
            else:
                retry_prompt = build_retry_prompt(source, scoped_violations, scoped_directives)
            feedback_context = self._feedback_context(failed_attempts)
            if initial_prompt.strip() and "STATE MACHINE ARCHITECT MODE" not in retry_prompt:
                retry_prompt = f"{initial_prompt.strip()}\n\nENGINE FEEDBACK:\n{retry_prompt}"
            if feedback_context:
                retry_prompt = f"{retry_prompt}\n\n{feedback_context}"
            delta_context = self._delta_context(diagnostic_deltas)
            if delta_context:
                retry_prompt = f"{retry_prompt}\n\n{delta_context}"
        if worker_name == "architect_llm" and len(violations) > 1:
            retry_prompt = (
                f"{retry_prompt}\n\n"
                "ARCHITECT MODE:\n"
                "- Consider the full finding set together.\n"
                "- Produce a coherent structural repair that satisfies every listed static and behavioral gate."
            )
        retry_prompt = budget_prompt(retry_prompt).text
        return retry_prompt, scoped_directives, scoped_violations

    def _suggest_human_decision(self, reason: str) -> str:
        suggestions = {
            "stagnant_repair": (
                "Escalate the payload to an architect model or edit the draft manually; the worker returned an unchanged repair."
            ),
            "architect_stagnant_after_escalation": (
                "Review manually or change the architect prompt/model settings; the escalated architect repair also returned unchanged code."
            ),
            "repair_strategy_manual_review": (
                "Review the strategy rationale and choose whether to relax policy, provide a stronger spec, or make a manual edit."
            ),
            "max_retries_exhausted": (
                "Escalate to an architect model with the blocking findings and diagnostic deltas before asking for human code review."
            ),
            "repair_supplier_error": (
                "Review the repair backend error, then retry with a different model, larger token budget, or a manual patch."
            ),
            "architect_static_gate_failed": (
                "Review the architect output manually; it was routed through the static engines and still failed a blocking gate."
            ),
            "metric_scope_ambiguous": (
                "Review the code manually; behavior passes, but the remaining branching failure may be module-wide helper complexity rather than target-function complexity."
            ),
            "branching_loop_detected": (
                "Review the looped branch state manually; the controller revisited the same unresolved branch pattern without progress."
            ),
            "no_new_artifact_progress": (
                "Review manually or escalate; the selected branch produced no changed artifact and unresolved requirements remain."
            ),
            "same_failure_after_branch_switch": (
                "Review the repair route manually; switching branch actions still led to the same unresolved failure."
            ),
            "small_worker_initial_timeout": (
                "The local worker timed out before returning code. Escalate to the architect or split the task into smaller function packets."
            ),
            "small_worker_initial_empty_response": (
                "The local worker returned no code. Escalate to the architect or reduce the prompt size."
            ),
            "small_worker_initial_backend_unreachable": (
                "The local worker backend was not reachable. Start the backend, switch models, or use architect fallback."
            ),
            "architect_after_backend_failure_failed": (
                "The architect fallback also failed after the local worker backend failure. Review API settings and retry manually."
            ),
            "architect_after_backend_failure_static_gate_failed": (
                "The architect produced code after a worker backend failure, but that code failed validation."
            ),
        }
        return suggestions.get(
            reason,
            "Review the blocking findings and decide whether to revise the prompt, relax policy, or edit the code directly.",
        )

    def _human_review_payload(self, reason: str, attempt: GenerationAttempt) -> HumanReviewPayload:
        violations = attempt.validation.get("violations", [])
        violation_engines = {violation.get("engine") for violation in violations if violation.get("engine")}
        blocking_findings = [
            finding
            for finding in attempt.findings
            if finding.get("severity") == "High" or finding.get("engine") in violation_engines
        ]
        return HumanReviewPayload(
            status="human_review_required",
            reason=reason,
            blocking_findings=blocking_findings,
            blocking_violations=violations,
            behavior_issues=attempt.behavior_validation.get("issues", []),
            formal_issues=attempt.formal_validation.get("issues", []),
            last_retry_prompt=attempt.retry_prompt,
            diagnostic_deltas=attempt.diagnostic_deltas,
            repair_directives=attempt.repair_directives,
            suggested_human_decision=self._suggest_human_decision(reason),
        )

    def _backend_failure_reason(self, exc: Exception) -> str:
        text = f"{exc.__class__.__name__}: {exc}".lower()
        if "timed out" in text or "timeout" in text:
            return "small_worker_initial_timeout"
        if "empty response" in text or "returned no" in text:
            return "small_worker_initial_empty_response"
        if "not reachable" in text or "connection" in text or "unreachable" in text:
            return "small_worker_initial_backend_unreachable"
        return "small_worker_initial_backend_failure"

    def _contract_summary_from_prompt(self, initial_prompt: str) -> list[str]:
        summary: list[str] = []
        for raw_line in initial_prompt.splitlines():
            line = raw_line.strip()
            if line.startswith("NAME: "):
                name = line.split(":", 1)[1].strip()
                if name:
                    summary.append(name)
        return summary

    def _backend_failure_payload(self, initial_prompt: str, exc: Exception) -> BackendFailurePayload:
        contract_summary = self._contract_summary_from_prompt(initial_prompt)
        return BackendFailurePayload(
            backend="small_worker",
            stage="initial_draft",
            reason=self._backend_failure_reason(exc),
            error=f"{exc.__class__.__name__}: {exc}",
            prompt_size=len(initial_prompt),
            contract_count=len(contract_summary),
            plan_packet=initial_prompt,
            contract_queue_summary=contract_summary,
        )

    def _backend_failure_attempt(
        self,
        target: str,
        initial_prompt: str,
        exc: Exception,
        retry_prompt: str = "",
        repair_error: str = "",
        repair_worker: str = "",
    ) -> GenerationAttempt:
        payload = self._backend_failure_payload(initial_prompt, exc)
        attempt = GenerationAttempt(
            attempt=0,
            draft="",
            findings=[],
            validation={
                "is_compliant": False,
                "violations": [
                    {
                        "kind": payload.reason,
                        "engine": "backend-small-worker",
                        "severity": "High",
                        "summary": "Small worker failed before producing a draft",
                        "rationale": payload.error,
                        "current_value": payload.backend,
                        "allowed_value": "backend returns complete Python code",
                        "repair_hint": "architect_fallback",
                        "evidence": asdict(payload),
                    }
                ],
            },
            behavior_validation={"is_compliant": True, "issues": []},
            formal_validation={"is_compliant": True, "skipped": True, "tool": "formal", "issues": []},
            retry_prompt=retry_prompt,
            repair_worker=repair_worker,
            repair_error=repair_error,
            draft_source_worker="backend_failure",
            changed=False,
            backend_failure=asdict(payload),
        )
        attempt.branch_state_signature = build_branch_state_signature(target, attempt).to_dict()
        return attempt

    def _handle_initial_backend_failure(
        self,
        target: str,
        initial_prompt: str,
        exc: Exception,
    ) -> AgentResult:
        reason = self._backend_failure_reason(exc)
        retry_prompt = build_backend_failure_architect_prompt(
            stage="initial_draft",
            backend="small_worker",
            error=f"{exc.__class__.__name__}: {exc}",
            plan_packet=initial_prompt,
            contract_count=len(self._contract_summary_from_prompt(initial_prompt)),
            contract_queue_summary=self._contract_summary_from_prompt(initial_prompt),
        )
        if self.architect_supplier is None:
            session = GenerationSession(target=target, route="backend_failure", max_retries=self.max_retries)
            attempt = self._backend_failure_attempt(target, initial_prompt, exc, retry_prompt=retry_prompt)
            session.attempts.append(attempt)
            session.final_status = "manual_review_required"
            session.human_review = self._human_review_payload(reason, attempt)
            return AgentResult(agent=self.name, payload=asdict(session))
        try:
            architect_draft = self.architect_supplier("", retry_prompt)
        except Exception as architect_exc:
            session = GenerationSession(target=target, route="backend_failure", max_retries=self.max_retries)
            attempt = self._backend_failure_attempt(
                target,
                initial_prompt,
                exc,
                retry_prompt=retry_prompt,
                repair_worker="architect_llm",
                repair_error=f"{architect_exc.__class__.__name__}: {architect_exc}",
            )
            session.attempts.append(attempt)
            session.final_status = "manual_review_required"
            session.human_review = self._human_review_payload("architect_after_backend_failure_failed", attempt)
            return AgentResult(agent=self.name, payload=asdict(session))
        backend_attempt = self._backend_failure_attempt(
            target,
            initial_prompt,
            exc,
            retry_prompt=retry_prompt,
            repair_worker="architect_llm",
        )
        return self.run(
            target=target,
            initial_prompt=retry_prompt,
            draft_override=architect_draft,
            draft_source_override="architect_llm",
            pre_attempts=[backend_attempt],
        )

    def run(
        self,
        target: str,
        initial_prompt: str,
        draft_override: str | None = None,
        draft_source_override: str = "draft_supplier",
        pre_attempts: list[GenerationAttempt] | None = None,
    ) -> AgentResult:
        if draft_override is None:
            try:
                initial_draft = self.draft_supplier(initial_prompt)
            except Exception as exc:
                return self._handle_initial_backend_failure(target, initial_prompt, exc)
        else:
            initial_draft = draft_override
        initial_findings = self._scan(initial_draft)
        route = self._route(initial_findings)
        session = GenerationSession(target=target, route=route, max_retries=self.max_retries)
        failed_attempts: list[GenerationAttempt] = list(pre_attempts or [])
        session.attempts.extend(failed_attempts)

        draft = initial_draft
        draft_source_worker = draft_source_override if draft_override is not None else "draft_supplier"
        previous_draft = ""
        for attempt_index in range(len(failed_attempts), self.max_retries + 1):
            findings = self._scan(draft)
            validation_result: ValidationResult = validate_findings(findings, policy=self.policy)
            if self.behavior_spec is None:
                behavior_validation = {"is_compliant": True, "issues": []}
            else:
                behavior_validation = serialize_behavior_result(
                    validate_function_behavior(
                        draft,
                        self.behavior_spec,
                        timeout_seconds=self.behavior_timeout_seconds
                        if self.behavior_timeout_seconds is not None
                        else 1.0,
                    )
                )
                if behavior_validation["is_compliant"]:
                    validation_result = validate_findings(
                        findings,
                        policy={
                            **(self.policy or {}),
                            "demote_behavior_verified_structural_findings": True,
                        },
                        behavior_verified=True,
                    )
            formal_validation = self._validate_formal_contracts(draft)
            is_complete = (
                validation_result.is_compliant
                and behavior_validation["is_compliant"]
                and formal_validation["is_compliant"]
            )
            retry_prompt = ""
            repair_worker = ""
            next_repair_supplier: RepairSupplier | None = None
            force_manual_review = False
            manual_review_reason = "repair_strategy_manual_review"
            changed = attempt_index == 0 or not self._is_stagnant(previous_draft, draft)
            diff_text = self._diff_text(previous_draft, draft) if attempt_index > 0 else ""
            diagnostic_deltas = self._diagnostic_deltas(
                failed_attempts[-1] if failed_attempts else None,
                validation_result.violations,
            )
            diagnostic_stagnant = self._is_diagnostic_stagnant(diagnostic_deltas)
            active_violations = (
                validation_result.violations
                if not validation_result.is_compliant
                else (
                    []
                    if behavior_validation["is_compliant"] and formal_validation["is_compliant"]
                    else (
                        self._behavior_violations(behavior_validation)
                        if not behavior_validation["is_compliant"]
                        else self._formal_violations(formal_validation)
                    )
                )
            )
            repair_directives = aggregate_violations(
                draft,
                active_violations,
                diagnostic_deltas=diagnostic_deltas,
            )
            if is_complete:
                self._debug_print(f"Iteration {attempt_index}: draft is static and behavior compliant.")
            elif validation_result.is_compliant:
                if not behavior_validation["is_compliant"]:
                    issue_summaries = ", ".join(
                        f"{issue['case']} expected {issue['expected']} got {issue['actual']}"
                        for issue in behavior_validation["issues"]
                    )
                    self._debug_print(f"Iteration {attempt_index}: behavior violation detected: {issue_summaries}.")
                else:
                    issue_summaries = ", ".join(
                        issue.get("summary", "formal validation failed")
                        for issue in formal_validation["issues"]
                    )
                    self._debug_print(f"Iteration {attempt_index}: formal violation detected: {issue_summaries}.")
            else:
                violation_summaries = ", ".join(
                    f"{violation.kind} ({violation.current_value} -> {violation.allowed_value})"
                    for violation in validation_result.violations
                )
                self._debug_print(f"Iteration {attempt_index}: violation detected: {violation_summaries}.")
            if (
                not is_complete
                and draft_source_worker == "architect_llm"
                and not validation_result.is_compliant
            ):
                force_manual_review = True
                if self._is_metric_scope_ambiguous(validation_result, behavior_validation, formal_validation):
                    manual_review_reason = "metric_scope_ambiguous"
                    self._debug_print(
                        "Architect output is behavior-compliant but still fails branching complexity. "
                        "Stopping for metric-scope manual review."
                    )
                else:
                    manual_review_reason = (
                        "architect_after_backend_failure_static_gate_failed"
                        if failed_attempts and failed_attempts[0].backend_failure
                        else "architect_static_gate_failed"
                    )
                    self._debug_print(
                        "Architect output failed static engine gates. Stopping instead of retrying architect."
                    )
            if not force_manual_review and not is_complete and attempt_index < self.max_retries:
                retry_violations = self._retry_violations(
                    validation_result,
                    behavior_validation,
                    formal_validation,
                )
                repair_worker, next_repair_supplier = self._repair_worker_for(len(failed_attempts))
                if (
                    diagnostic_stagnant
                    and repair_worker == "small_worker"
                    and self.architect_supplier is not None
                ):
                    repair_worker = "architect_llm"
                    next_repair_supplier = self.architect_supplier
                    self._debug_print(
                        "Repeated diagnostics did not improve. Escalating the next repair to architect."
                    )
                retry_prompt, prompt_directives, prompt_violations = self._build_scoped_retry_prompt(
                    source=draft,
                    violations=retry_violations,
                    diagnostic_deltas=diagnostic_deltas,
                    worker_name=repair_worker,
                    initial_prompt=initial_prompt,
                    failed_attempts=failed_attempts,
                )
                repair_directives = prompt_directives
                if self.repair_strategy is not None:
                    decision = self.repair_strategy.decide(
                        source=draft,
                        violations=prompt_violations if not validation_result.is_compliant else [],
                        behavior_issues=(
                            behavior_validation["issues"]
                            if validation_result.is_compliant and not behavior_validation["is_compliant"]
                            else formal_validation["issues"]
                            if validation_result.is_compliant and behavior_validation["is_compliant"]
                            else []
                        ),
                        attempt_index=attempt_index,
                        max_retries=self.max_retries,
                    )
                    if decision.mode == MANUAL_REVIEW:
                        force_manual_review = True
                        manual_review_reason = "repair_strategy_manual_review"
                        self._debug_print(f"Repair strategy selected manual review: {decision.rationale}")
                    else:
                        self._debug_print(f"Repair strategy selected model-only repair: {decision.rationale}")
                    if decision.repair_instructions and repair_worker != "small_worker":
                        retry_prompt = (
                            f"{retry_prompt}\n\n"
                            "TARGETED REPAIR INSTRUCTIONS:\n"
                            + "\n".join(f"- {instruction}" for instruction in decision.repair_instructions)
                        )
                        retry_prompt = budget_prompt(retry_prompt).text
                self._debug_print(f"Sending repair prompt to {repair_worker}.")
            attempt = GenerationAttempt(
                attempt=attempt_index,
                draft=draft,
                findings=[asdict(finding) for finding in findings],
                validation=serialize_validation_result(validation_result),
                behavior_validation=behavior_validation,
                formal_validation=formal_validation,
                diagnostic_deltas=diagnostic_deltas,
                repair_directives=serialize_repair_directives(repair_directives),
                retry_prompt=retry_prompt,
                repair_worker=repair_worker,
                draft_source_worker=draft_source_worker,
                changed=changed,
                diff=diff_text,
                diagnostic_stagnant=diagnostic_stagnant,
            )
            attempt.branch_state_signature = build_branch_state_signature(target, attempt).to_dict()
            session.attempts.append(attempt)
            if is_complete:
                session.final_status = "completed"
                break
            if not force_manual_review:
                branch_loop = detect_branching_loop(session.attempts)
                if branch_loop.detected:
                    attempt.branch_loop = branch_loop.to_dict()
                    force_manual_review = True
                    manual_review_reason = branch_loop.reason
                    self._debug_print(f"Branching loop detected: {branch_loop.message}")
            if force_manual_review:
                session.final_status = "manual_review_required"
                session.human_review = self._human_review_payload(manual_review_reason, attempt)
                break
            if attempt_index < self.max_retries:
                failed_attempts.append(attempt)
                worker_name = attempt.repair_worker
                supplier = next_repair_supplier
                if not worker_name or supplier is None:
                    worker_name, supplier = self._repair_worker_for(len(failed_attempts) - 1)
                try:
                    next_draft = supplier(draft, retry_prompt)
                except Exception as exc:
                    attempt.repair_worker = worker_name
                    attempt.repair_error = f"{exc.__class__.__name__}: {exc}"
                    self._debug_print(f"Repair backend failed in {worker_name}: {attempt.repair_error}")
                    session.final_status = "manual_review_required"
                    session.human_review = self._human_review_payload("repair_supplier_error", attempt)
                    break
                stagnant_repair = self._is_stagnant(draft, next_draft)
                diagnostic_stagnant_repair = (
                    self._is_diagnostic_stagnant(diagnostic_deltas)
                    and worker_name != "architect_llm"
                )
                if stagnant_repair or diagnostic_stagnant_repair:
                    if worker_name != "architect_llm":
                        fallback_worker, fallback_supplier = self._repair_worker_for(len(failed_attempts))
                        if fallback_worker == "architect_llm":
                            self._debug_print(
                                "Small worker returned unchanged code. Escalating repair prompt to architect."
                            )
                            attempt.repair_worker = "small_worker->architect_llm"
                            architect_violations = (
                                validation_result.violations
                                if not validation_result.is_compliant
                                else (
                                    self._behavior_violations(behavior_validation)
                                    if not behavior_validation["is_compliant"]
                                    else self._formal_violations(formal_validation)
                                )
                            )
                            retry_prompt, architect_directives, _architect_violations = self._build_scoped_retry_prompt(
                                source=draft,
                                violations=architect_violations,
                                diagnostic_deltas=diagnostic_deltas,
                                worker_name=fallback_worker,
                                initial_prompt=initial_prompt,
                                failed_attempts=failed_attempts,
                            )
                            attempt.retry_prompt = retry_prompt
                            attempt.repair_directives = serialize_repair_directives(architect_directives)
                            try:
                                next_draft = fallback_supplier(draft, retry_prompt)
                            except Exception as exc:
                                attempt.repair_error = f"{exc.__class__.__name__}: {exc}"
                                self._debug_print(
                                    f"Repair backend failed in {fallback_worker}: {attempt.repair_error}"
                                )
                                session.final_status = "manual_review_required"
                                session.human_review = self._human_review_payload("repair_supplier_error", attempt)
                                break
                            if not self._is_stagnant(draft, next_draft):
                                self._debug_print("Architect attempt received. Re-analyzing updated draft.")
                                previous_draft = draft
                                draft = next_draft
                                draft_source_worker = "architect_llm"
                                continue
                    stagnation_reason = (
                        "architect_stagnant_after_escalation"
                        if worker_name == "architect_llm" or attempt.repair_worker == "small_worker->architect_llm"
                        else "stagnant_repair"
                    )
                    self._debug_print("Warning: No changes detected in code. Terminating to avoid infinite loop.")
                    session.final_status = "manual_review_required"
                    session.human_review = self._human_review_payload(stagnation_reason, attempt)
                    break
                self._debug_print("Attempt received. Re-analyzing updated draft.")
                previous_draft = draft
                draft = next_draft
                draft_source_worker = worker_name
        else:
            session.final_status = "manual_review_required"

        if session.final_status != "completed":
            session.final_status = "manual_review_required"
            if session.human_review is None and session.attempts:
                session.human_review = self._human_review_payload("max_retries_exhausted", session.attempts[-1])

        return AgentResult(agent=self.name, payload=asdict(session))

    def _feedback_context(self, failed_attempts: list[GenerationAttempt]) -> str:
        if not failed_attempts:
            return ""
        lines = ["PRIOR FAILED ATTEMPTS:"]
        for attempt in failed_attempts:
            lines.append(f"- Attempt {attempt.attempt}:")
            for violation in attempt.validation.get("violations", []):
                lines.append(
                    f"  Static failure: {violation['kind']} had {violation['current_value']}; required {violation['allowed_value']}."
                )
            for issue in attempt.behavior_validation.get("issues", []):
                lines.append(
                    f"  Behavior failure: {issue['case']} expected {issue['expected']} but got {issue['actual']} ({issue['details']})."
                )
            for issue in attempt.formal_validation.get("issues", []):
                lines.append(
                    f"  Formal failure: {issue.get('summary', 'formal validation failed')} ({issue.get('details', '')})."
                )
        lines.append("Do not repeat any prior failed pattern.")
        return "\n".join(lines)
