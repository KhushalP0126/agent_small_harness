from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import unified_diff
from typing import Callable

from agents.base import AgentResult, BaseAgent
from agents.engine_registry import EngineRegistry
from agents.parse_contract import ParseContractAgent, ParseFailure
from agents.repair_strategy import MANUAL_REVIEW, TEMPLATE_DIRECTED, RepairStrategyAgent
from engines.base import EngineFinding
from prompt.retry_builder import build_retry_prompt
from validation.behavior import FunctionBehaviorSpec, serialize_behavior_result, validate_function_behavior
from validation.policy import serialize_validation_result, validate_findings
from validation.types import ValidationResult, Violation


DraftSupplier = Callable[[str], str]
RepairSupplier = Callable[[str, str], str]


@dataclass
class GenerationAttempt:
    attempt: int
    draft: str
    findings: list[dict]
    validation: dict
    behavior_validation: dict
    diagnostic_deltas: list[dict] = field(default_factory=list)
    retry_prompt: str = ""
    changed: bool = True
    diff: str = ""


@dataclass
class GenerationSession:
    target: str
    route: str
    max_retries: int
    attempts: list[GenerationAttempt] = field(default_factory=list)
    final_status: str = "manual_review_required"


class GenerationController(BaseAgent):
    name = "agent-generation-controller"

    def __init__(
        self,
        max_retries: int = 2,
        draft_supplier: DraftSupplier | None = None,
        repair_supplier: RepairSupplier | None = None,
        policy: dict | None = None,
        behavior_spec: FunctionBehaviorSpec | None = None,
        debug: bool = False,
        engine_registry: EngineRegistry | None = None,
        parse_contract: ParseContractAgent | None = None,
        repair_strategy: RepairStrategyAgent | None = None,
        language: str | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.draft_supplier = draft_supplier or (lambda prompt: prompt)
        self.repair_supplier = repair_supplier or (lambda draft, retry_prompt: draft)
        self.policy = policy
        self.behavior_spec = behavior_spec
        self.debug = debug
        self.engine_registry = engine_registry or EngineRegistry.default()
        self.parse_contract = parse_contract or ParseContractAgent()
        self.repair_strategy = repair_strategy
        self.language = language

    def _scan(self, source: str) -> list[EngineFinding]:
        parse_result = self.parse_contract.parse(source, language=self.language)
        if isinstance(parse_result, ParseFailure):
            return [parse_result.finding]
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

    def _debug_print(self, message: str) -> None:
        if self.debug:
            print(f"[Buddy] {message}")

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

    def run(self, target: str, initial_prompt: str) -> AgentResult:
        initial_draft = self.draft_supplier(initial_prompt)
        initial_findings = self._scan(initial_draft)
        route = self._route(initial_findings)
        session = GenerationSession(target=target, route=route, max_retries=self.max_retries)

        draft = initial_draft
        previous_draft = ""
        failed_attempts: list[GenerationAttempt] = []
        for attempt_index in range(self.max_retries + 1):
            findings = self._scan(draft)
            validation_result: ValidationResult = validate_findings(findings, policy=self.policy)
            if self.behavior_spec is None:
                behavior_validation = {"is_compliant": True, "issues": []}
            else:
                behavior_validation = serialize_behavior_result(
                    validate_function_behavior(draft, self.behavior_spec)
                )
            is_complete = validation_result.is_compliant and behavior_validation["is_compliant"]
            retry_prompt = ""
            force_manual_review = False
            changed = attempt_index == 0 or not self._is_stagnant(previous_draft, draft)
            diff_text = self._diff_text(previous_draft, draft) if attempt_index > 0 else ""
            diagnostic_deltas = self._diagnostic_deltas(
                failed_attempts[-1] if failed_attempts else None,
                validation_result.violations,
            )
            if is_complete:
                self._debug_print(f"Iteration {attempt_index}: draft is static and behavior compliant.")
            elif validation_result.is_compliant:
                issue_summaries = ", ".join(
                    f"{issue['case']} expected {issue['expected']} got {issue['actual']}"
                    for issue in behavior_validation["issues"]
                )
                self._debug_print(f"Iteration {attempt_index}: behavior violation detected: {issue_summaries}.")
            else:
                violation_summaries = ", ".join(
                    f"{violation.kind} ({violation.current_value} -> {violation.allowed_value})"
                    for violation in validation_result.violations
                )
                self._debug_print(f"Iteration {attempt_index}: violation detected: {violation_summaries}.")
            if not is_complete and attempt_index < self.max_retries:
                retry_violations = (
                    validation_result.violations
                    if not validation_result.is_compliant
                    else self._behavior_violations(behavior_validation)
                )
                retry_prompt = build_retry_prompt(draft, retry_violations)
                if self.repair_strategy is not None:
                    decision = self.repair_strategy.decide(
                        source=draft,
                        violations=validation_result.violations,
                        behavior_issues=behavior_validation["issues"],
                        attempt_index=attempt_index,
                        max_retries=self.max_retries,
                    )
                    if decision.mode == MANUAL_REVIEW:
                        force_manual_review = True
                        self._debug_print(f"Repair strategy selected manual review: {decision.rationale}")
                    elif decision.mode == TEMPLATE_DIRECTED and decision.template_code:
                        retry_prompt = (
                            f"{retry_prompt}\n\n"
                            f"TEMPLATE-DIRECTED REPAIR ({decision.template_name}):\n"
                            f"{decision.template_code}"
                        )
                        self._debug_print(
                            f"Repair strategy selected template-directed repair: {decision.template_name}"
                        )
                    else:
                        self._debug_print(f"Repair strategy selected model-only repair: {decision.rationale}")
                    if decision.repair_instructions:
                        retry_prompt = (
                            f"{retry_prompt}\n\n"
                            "TARGETED REPAIR INSTRUCTIONS:\n"
                            + "\n".join(f"- {instruction}" for instruction in decision.repair_instructions)
                        )
                feedback_context = self._feedback_context(failed_attempts)
                if initial_prompt.strip():
                    retry_prompt = f"{initial_prompt.strip()}\n\nENGINE FEEDBACK:\n{retry_prompt}"
                if feedback_context:
                    retry_prompt = f"{retry_prompt}\n\n{feedback_context}"
                delta_context = self._delta_context(diagnostic_deltas)
                if delta_context:
                    retry_prompt = f"{retry_prompt}\n\n{delta_context}"
                self._debug_print("Sending repair prompt to model.")
            attempt = GenerationAttempt(
                attempt=attempt_index,
                draft=draft,
                findings=[asdict(finding) for finding in findings],
                validation=serialize_validation_result(validation_result),
                behavior_validation=behavior_validation,
                diagnostic_deltas=diagnostic_deltas,
                retry_prompt=retry_prompt,
                changed=changed,
                diff=diff_text,
            )
            session.attempts.append(attempt)
            if is_complete:
                session.final_status = "completed"
                break
            if force_manual_review:
                session.final_status = "manual_review_required"
                break
            if attempt_index < self.max_retries:
                failed_attempts.append(attempt)
                next_draft = self.repair_supplier(draft, retry_prompt)
                if self._is_stagnant(draft, next_draft):
                    self._debug_print("Warning: No changes detected in code. Terminating to avoid infinite loop.")
                    session.final_status = "manual_review_required"
                    break
                self._debug_print("Attempt received. Re-analyzing updated draft.")
                previous_draft = draft
                draft = next_draft
        else:
            session.final_status = "manual_review_required"

        if session.final_status != "completed":
            session.final_status = "manual_review_required"

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
        lines.append("Do not repeat any prior failed pattern.")
        return "\n".join(lines)
