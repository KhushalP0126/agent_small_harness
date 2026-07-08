from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from agents.base import AgentResult, BaseAgent
from agents.prompt_normalizer import PromptNormalizerAgent
from agents.task_classifier import TaskClassifierAgent, TaskClassification


CALL_EXPECTATION_RE = re.compile(
    r"(?P<function>[A-Za-z_][A-Za-z0-9_]*)\((?P<args>.*?)\)\s*(?:==|->|=>|returns?)\s*(?P<expected>[^;\n]+)",
    re.IGNORECASE,
)
FUNCTION_NAME_RE = re.compile(
    r"(?:function|method|helper)\s+(?:named|called)\s+`?(?P<name>[A-Za-z_][A-Za-z0-9_]*)`?",
    re.IGNORECASE,
)
DEF_NAME_RE = re.compile(r"\bdef\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
SNAKE_NAME_RE = re.compile(r"\b(?P<name>[a-z_]+[a-z0-9_]*)\s*\(")


@dataclass
class PlannedBehaviorCase:
    name: str
    call: str
    expected: str


@dataclass
class PlanSpec:
    raw_prompt: str
    normalized_prompt: str
    goal: str
    task_type: str
    language: str
    target_function: str = ""
    behavior_cases: list[PlannedBehaviorCase] = field(default_factory=list)
    deal_contracts: list[str] = field(default_factory=list)
    adapter_contracts: list[str] = field(default_factory=list)
    allowed_libraries: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)
    performance_constraints: list[str] = field(default_factory=list)
    security_constraints: list[str] = field(default_factory=list)
    state_machine_constraints: list[str] = field(default_factory=list)
    dependency_graph_context: list[str] = field(default_factory=list)
    needs_user_clarification: bool = False
    questions: list[str] = field(default_factory=list)
    route_hint: str = "small_worker"


class PlanModeAgent(BaseAgent):
    """Turns user intent into a compact, harness-ready task spec.

    This layer is deliberately deterministic. Its job is not to solve the task;
    it narrows vague input into explicit fields the preprocessor, engines, and
    model loop can consume or reject before generation starts.
    """

    name = "agent-plan-mode"

    def __init__(
        self,
        normalizer: PromptNormalizerAgent | None = None,
        classifier: TaskClassifierAgent | None = None,
    ) -> None:
        self.normalizer = normalizer or PromptNormalizerAgent()
        self.classifier = classifier or TaskClassifierAgent()

    def plan(self, prompt: str) -> PlanSpec:
        normalized = self.normalizer.normalize(prompt)
        classification = self.classifier.classify(normalized.normalized_prompt)
        target_function = self._target_function(normalized.normalized_prompt)
        behavior_cases = self._behavior_cases(prompt)
        deal_contracts = self._deal_contracts(
            language=classification.language,
            target_function=target_function,
            behavior_cases=behavior_cases,
        )
        allowed_libraries = self._allowed_libraries(classification)
        adapter_contracts = self._adapter_contracts(allowed_libraries)
        forbidden_patterns = [
            "imports unless explicitly allowed",
            "file I/O",
            "network calls",
            "eval/exec",
            "global mutable state",
            "print/demo code",
        ]
        performance_constraints = self._performance_constraints(normalized.normalized_prompt)
        security_constraints = self._security_constraints(normalized.normalized_prompt)
        state_machine_constraints = self._state_machine_constraints(normalized.normalized_prompt)
        dependency_graph_context = self._dependency_graph_context(normalized.normalized_prompt)
        questions = self._questions(
            normalized_prompt=normalized.normalized_prompt,
            classification=classification,
            target_function=target_function,
            behavior_cases=behavior_cases,
        )
        return PlanSpec(
            raw_prompt=prompt,
            normalized_prompt=normalized.normalized_prompt,
            goal=normalized.normalized_prompt,
            task_type=classification.task_type,
            language=classification.language,
            target_function=target_function,
            behavior_cases=behavior_cases,
            deal_contracts=deal_contracts,
            adapter_contracts=adapter_contracts,
            allowed_libraries=allowed_libraries,
            forbidden_patterns=forbidden_patterns,
            performance_constraints=performance_constraints,
            security_constraints=security_constraints,
            state_machine_constraints=state_machine_constraints,
            dependency_graph_context=dependency_graph_context,
            needs_user_clarification=bool(questions),
            questions=questions,
            route_hint=classification.route_hint,
        )

    def _target_function(self, prompt: str) -> str:
        for pattern in (DEF_NAME_RE, FUNCTION_NAME_RE, SNAKE_NAME_RE):
            match = pattern.search(prompt)
            if match:
                return match.group("name")
        return ""

    def _behavior_cases(self, prompt: str) -> list[PlannedBehaviorCase]:
        cases: list[PlannedBehaviorCase] = []
        for index, match in enumerate(CALL_EXPECTATION_RE.finditer(prompt), start=1):
            call = f"{match.group('function')}({match.group('args').strip()})"
            expected = match.group("expected").strip().rstrip(".")
            cases.append(
                PlannedBehaviorCase(
                    name=f"case_{index}",
                    call=call,
                    expected=expected,
                )
            )
        return cases

    def _deal_contracts(
        self,
        language: str,
        target_function: str,
        behavior_cases: list[PlannedBehaviorCase],
    ) -> list[str]:
        if language != "python" or not target_function:
            return []
        contracts = [
            "import deal",
            f"# Attach these plan-layer contracts to `{target_function}` before implementation.",
        ]
        for case in behavior_cases:
            contracts.append(f"@deal.example(lambda: {case.call} == {case.expected})")
        if behavior_cases:
            contracts.append("# Architect may add stronger @deal.pre/@deal.post clauses when requirements are explicit.")
        return contracts

    def _allowed_libraries(self, classification: TaskClassification) -> list[str]:
        if classification.language != "python":
            return []
        return classification.libraries

    def _adapter_contracts(self, libraries: list[str]) -> list[str]:
        contracts: list[str] = []
        for library in libraries:
            contracts.extend(
                [
                    f"treat `{library}` as an opaque dependency behind local adapter helpers",
                    f"validate data entering and leaving `{library}` adapter helpers with explicit examples or schemas",
                    f"do not ask the small worker to reason about `{library}` internals",
                ]
            )
        return contracts

    def _performance_constraints(self, prompt: str) -> list[str]:
        lowered = prompt.lower()
        constraints = [
            "loop nesting depth <= 2",
            "cyclomatic complexity <= 7",
        ]
        if any(token in lowered for token in ("efficient", "performance", "fast", "scalable", "large input")):
            constraints.append("avoid repeated linear membership checks inside loops")
        if "sort" in lowered or "sorted" in lowered:
            constraints.append("preserve required ordering semantics")
        return constraints

    def _security_constraints(self, prompt: str) -> list[str]:
        lowered = prompt.lower()
        constraints = [
            "no eval or exec",
            "no shell execution",
            "no network access",
        ]
        if "user input" in lowered or "parse" in lowered or "validate" in lowered:
            constraints.append("handle malformed input explicitly")
        return constraints

    def _state_machine_constraints(self, prompt: str) -> list[str]:
        lowered = prompt.lower()
        is_line_parser = any(
            token in lowered
            for token in (
                "configuration",
                "config",
                "line",
                "lines",
                "section",
                "header",
                "key=value",
                "key value",
                "current section",
                "equals sign",
                "empty key",
            )
        )
        is_token_parser = any(
            token in lowered
            for token in (
                "comma-separated",
                "comma separated",
                "token",
                "tokens",
                "signed integer",
                "integer tokens",
                "parse_int_list",
            )
        )
        if not is_line_parser and not is_token_parser:
            return []

        constraints: list[str] = []
        if is_token_parser:
            constraints.extend(
                [
                    "split input into comma-separated tokens before validation",
                    "strip whitespace from each token before validation",
                    "accept optional leading + or - only when the remaining characters are digits",
                    "ignore empty tokens and non-integer tokens",
                    "append converted integers in original token order",
                ]
            )
            if not is_line_parser:
                return constraints

        constraints.extend(
            [
                "process one stripped input line at a time",
                "skip blank lines before applying parser-specific rules",
            ]
        )
        if "#" in prompt or "comment" in lowered:
            constraints.append("skip comment lines before applying parser-specific rules")
        if "section" in lowered or "header" in lowered:
            constraints.extend(
                [
                    "track parser state explicitly with an active section variable initialized to None",
                    "only activate section state after a valid non-empty section header is found",
                    "ignore key/value records until an active section exists",
                ]
            )
        has_key_value_rules = any(
            token in lowered
            for token in (
                "key=value",
                "key value",
                "equals sign",
                "exactly one equals",
                "empty key",
            )
        )
        if has_key_value_rules:
            constraints.extend(
                [
                    "treat a key/value record as valid only when it contains exactly one equals sign",
                    "trim key and value whitespace before storing",
                    "ignore key/value records with an empty key after trimming",
                ]
            )
        if "overwrite" in lowered or "later" in lowered:
            constraints.append("store later valid records over earlier records with the same key")
        return constraints

    def _dependency_graph_context(self, prompt: str) -> list[str]:
        lowered = prompt.lower()
        if (
            any(token in lowered for token in ("section", "header", "active section", "current section"))
            and any(token in lowered for token in ("key=value", "key value", "equals sign", "nested dict"))
        ):
            return [
                "lines -> active section state -> nested dict writes",
                "valid section header -> active_section changes",
                "valid key/value record -> result[active_section][key] write",
                "later valid records -> overwrite previous value for same section/key",
            ]
        if not (
            "event" in lowered
            and "state" in lowered
            and "emit" in lowered
            and any(token in lowered for token in ("reset", "add", "subtract"))
        ):
            return []
        graph = ["events -> state transitions -> emitted totals"]
        if "reset" in lowered:
            graph.append("reset -> total = 0")
        if "add" in lowered:
            graph.append("add -> total += value")
        if "subtract" in lowered:
            graph.append("subtract -> total -= value")
        if "emit" in lowered:
            graph.append("emit -> append current total")
        return graph

    def _questions(
        self,
        normalized_prompt: str,
        classification: TaskClassification,
        target_function: str,
        behavior_cases: list[PlannedBehaviorCase],
    ) -> list[str]:
        questions: list[str] = []
        lowered = normalized_prompt.lower()
        if classification.language != "python":
            questions.append("Should this run through Python-only generation now, or should this task be gated for manual review?")
        if classification.needs_behavior_spec and not target_function:
            questions.append("What exact function name should the generated code define?")
        if classification.needs_behavior_spec and not behavior_cases:
            questions.append("What input/output examples should the behavior validator enforce?")
        if any(word in lowered for word in ("app", "service", "full stack", "multi-file", "multifile")):
            questions.append("Which files or modules are in scope for this task?")
        return questions

    def to_prompt_context(self, spec: PlanSpec) -> str:
        lines = [
            "PLAN MODE SPEC:",
            f"- Goal: {spec.goal}",
            f"- Language: {spec.language}",
            f"- Task type: {spec.task_type}",
        ]
        if spec.target_function:
            lines.append(f"- Target function: {spec.target_function}")
        if spec.allowed_libraries:
            lines.append(f"- Allowed libraries: {', '.join(spec.allowed_libraries)}")
        if spec.adapter_contracts:
            lines.append("- Dependency adapter contracts:")
            lines.extend(f"  - {item}" for item in spec.adapter_contracts)
        lines.append("- Forbidden patterns:")
        lines.extend(f"  - {item}" for item in spec.forbidden_patterns)
        lines.append("- Performance constraints:")
        lines.extend(f"  - {item}" for item in spec.performance_constraints)
        lines.append("- Security constraints:")
        lines.extend(f"  - {item}" for item in spec.security_constraints)
        if spec.state_machine_constraints:
            lines.append("- State-machine constraints:")
            lines.extend(f"  - {item}" for item in spec.state_machine_constraints)
        if spec.dependency_graph_context:
            lines.append("- Dependency graph context:")
            lines.extend(f"  - {item}" for item in spec.dependency_graph_context)
        if spec.behavior_cases:
            lines.append("- Behavior examples:")
            lines.extend(f"  - {case.call} == {case.expected}" for case in spec.behavior_cases)
        if spec.deal_contracts:
            lines.append("- Deal contract candidates:")
            lines.extend(f"  {contract}" for contract in spec.deal_contracts)
        if spec.questions:
            lines.append("- Clarification questions:")
            lines.extend(f"  - {question}" for question in spec.questions)
        return "\n".join(lines)

    def to_worker_packet(self, spec: PlanSpec) -> str:
        """Render a compact MOSI packet for small local workers."""
        lines = ["PLAN PACKET:"]
        if spec.target_function:
            lines.append(f"FUNCTION: {spec.target_function}")
        lines.append(f"LANGUAGE: {spec.language}")
        if spec.behavior_cases:
            lines.append("EXAMPLES:")
            lines.extend(f"- {case.call} == {case.expected}" for case in spec.behavior_cases)
        if spec.state_machine_constraints:
            lines.append("STATE RULES:")
            lines.extend(f"- {item}" for item in spec.state_machine_constraints)
        if spec.dependency_graph_context:
            lines.append("DEPENDENCY GRAPH:")
            lines.extend(f"- {item}" for item in spec.dependency_graph_context)
        if spec.adapter_contracts:
            lines.append("ADAPTER RULES:")
            lines.extend(f"- {item}" for item in spec.adapter_contracts)
        if spec.performance_constraints:
            lines.append("PERFORMANCE RULES:")
            lines.extend(f"- {item}" for item in spec.performance_constraints)
        if spec.security_constraints:
            lines.append("SAFETY RULES:")
            lines.extend(f"- {item}" for item in spec.security_constraints)
        lines.append("FINAL RULES:")
        lines.append("- Return only complete Python code.")
        lines.append("- Preserve the requested public function name.")
        lines.append("- Do not add imports unless the plan explicitly allowed that library.")
        lines.append("- Do not use file I/O, network calls, eval, exec, print calls, or global mutable state.")
        return "\n".join(lines)

    def run(self, prompt: str) -> AgentResult:
        spec = self.plan(prompt)
        payload = asdict(spec)
        payload["prompt_context"] = self.to_prompt_context(spec)
        payload["worker_packet"] = self.to_worker_packet(spec)
        return AgentResult(agent=self.name, payload=payload)
