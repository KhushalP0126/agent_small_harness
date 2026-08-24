from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from agents.template_loader import TemplateLibrary


MODEL_ONLY = "model_only"
MANUAL_REVIEW = "manual_review"
DETERMINISTIC_TRANSFORM = "deterministic_transform"
JSON_PATCH = "json_patch"
REPAIR_MODES = (MODEL_ONLY, DETERMINISTIC_TRANSFORM, JSON_PATCH, MANUAL_REVIEW)


@dataclass
class RepairDecision:
    mode: str
    template_name: str = ""
    template_code: str = ""
    rationale: str = ""
    repair_instructions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SymbolReplacementPatch:
    target_symbol: str
    action: str
    replacement_source: str


def parse_symbol_replacement_patch(payload: str | dict[str, Any]) -> SymbolReplacementPatch:
    data = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(data, dict):
        raise ValueError("JSON patch must be an object")
    patch = SymbolReplacementPatch(
        target_symbol=str(data.get("target_symbol", "")).strip(),
        action=str(data.get("action", "")).strip(),
        replacement_source=str(data.get("replacement_source", "")).strip(),
    )
    if not patch.target_symbol or patch.action != "replace_symbol" or not patch.replacement_source:
        raise ValueError("Patch requires target_symbol, action=replace_symbol, and replacement_source")
    return patch


def apply_symbol_replacement_patch(source: str, patch: SymbolReplacementPatch, language: str = "python") -> str:
    if language != "python":
        raise ValueError("Typed symbol replacement currently supports Python only")
    tree = ast.parse(source)
    replacement_tree = ast.parse(patch.replacement_source)
    replacements = [node for node in replacement_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    if len(replacements) != 1 or replacements[0].name != patch.target_symbol:
        raise ValueError("Replacement must define exactly the target symbol")
    targets = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == patch.target_symbol]
    if len(targets) != 1:
        raise ValueError("Target symbol must exist exactly once at module scope")
    target = targets[0]
    lines = source.splitlines(keepends=True)
    replacement = patch.replacement_source.rstrip() + ("\n" if lines[target.end_lineno - 1].endswith("\n") else "")
    candidate = "".join(lines[: target.lineno - 1]) + replacement + "".join(lines[target.end_lineno :])
    ast.parse(candidate)
    return candidate


def apply_deterministic_transform(source: str, violation: Any, language: str = "python") -> str:
    """Apply only evidence-backed transforms whose target is unambiguous."""
    if language != "python":
        raise ValueError("Deterministic transforms currently support Python only")
    kind = violation.get("kind", "") if isinstance(violation, dict) else getattr(violation, "kind", "")
    evidence = violation.get("evidence", {}) if isinstance(violation, dict) else getattr(violation, "evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("Deterministic transform requires structured evidence")
    tree = ast.parse(source)

    if kind in {"external_dependency", "import_risk_block"}:
        metrics = evidence.get("metrics", {})
        imports = metrics.get("imports", []) if isinstance(metrics, dict) else []
        forbidden = str(
            evidence.get("module")
            or evidence.get("forbidden_import")
            or (imports[0] if len(imports) == 1 else "")
        )
        if not forbidden:
            raise ValueError("Forbidden-import transform requires an exact module")
        kept = []
        removed = 0
        for node in tree.body:
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(name == forbidden or name.startswith(forbidden + ".") for name in names):
                removed += 1
            else:
                kept.append(node)
        if removed != 1:
            raise ValueError("Forbidden import must match exactly one statement")
        tree.body = kept
    elif kind == "unsafe_call":
        unsafe = str(evidence.get("unsafe_api") or evidence.get("call") or "")
        safe = _safe_alternative(violation)
        if not unsafe or not safe:
            raise ValueError("Unsafe-call transform requires exact unsafe and safe API names")
        transformer = _CallReplacement(unsafe, safe)
        tree = transformer.visit(tree)
        if transformer.replacements != 1:
            raise ValueError("Unsafe API must match exactly one call")
    elif kind == "bounds_risk":
        target = str(evidence.get("target_expression") or "")
        replacement = str(evidence.get("replacement_expression") or "")
        if not target or not replacement or not evidence.get("counterexample") or not evidence.get("policy_directive"):
            raise ValueError("Bounds transform requires target, replacement, counterexample, and policy directive")
        transformer = _ExpressionReplacement(target, replacement)
        tree = transformer.visit(tree)
        if transformer.replacements != 1:
            raise ValueError("Bounds expression must match exactly once")
    else:
        raise ValueError(f"No deterministic transform registered for {kind}")

    ast.fix_missing_locations(tree)
    candidate = ast.unparse(tree) + "\n"
    ast.parse(candidate)
    return candidate


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else ""
    return ""


def _name_node(dotted: str) -> ast.expr:
    parts = dotted.split(".")
    node: ast.expr = ast.Name(id=parts[0], ctx=ast.Load())
    for part in parts[1:]:
        node = ast.Attribute(value=node, attr=part, ctx=ast.Load())
    return node


class _CallReplacement(ast.NodeTransformer):
    def __init__(self, unsafe: str, safe: str) -> None:
        self.unsafe = unsafe
        self.safe = safe
        self.replacements = 0

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if _dotted_name(node.func) == self.unsafe:
            node.func = _name_node(self.safe)
            self.replacements += 1
        return node


class _ExpressionReplacement(ast.NodeTransformer):
    def __init__(self, target: str, replacement: str) -> None:
        self.target = ast.dump(ast.parse(target, mode="eval").body, include_attributes=False)
        self.replacement = ast.parse(replacement, mode="eval").body
        self.replacements = 0

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if ast.dump(node, include_attributes=False) == self.target:
            self.replacements += 1
            return self.replacement
        return super().generic_visit(node)


def _violation_kinds(violations: Iterable[Any]) -> set[str]:
    kinds: set[str] = set()
    for violation in violations or []:
        if isinstance(violation, dict):
            kind = violation.get("kind", "")
        else:
            kind = getattr(violation, "kind", "")
        if kind:
            kinds.add(kind)
    return kinds


def _behavior_issue_count(behavior_issues: Iterable[Any]) -> int:
    return sum(1 for _issue in behavior_issues or [])


def _diagnostic_refactor(violation: Any) -> str:
    evidence = violation.get("evidence", {}) if isinstance(violation, dict) else getattr(violation, "evidence", {})
    diagnostic = evidence.get("diagnostic", {}) if isinstance(evidence, dict) else {}
    return diagnostic.get("recommended_refactor", "") if isinstance(diagnostic, dict) else ""


class RepairStrategyAgent:
    """Decides how to repair a draft.

    This is the self-correction layer: it inspects the static violations and behavior
    issues, then chooses between letting the model repair freely (``model_only``),
    steering it with a pre-validated template (``template_directed``), or bailing out
    to ``manual_review`` when there is no actionable path.
    """

    name = "agent-repair-strategy"

    def select_initial_template(self, source: str, forced_template: str = "") -> tuple[str, str]:
        del source, forced_template
        return "", ""

    def select_skeleton(
        self,
        task: str,
        language: str,
        library: TemplateLibrary | None = None,
    ) -> str:
        """Return a language-specific skeletal seed for a task, or "" if none exists."""
        library = library or TemplateLibrary()
        return library.load(task, language) or ""

    def repair_instructions_for(
        self,
        violations: Iterable[Any] | None = None,
        behavior_issues: Iterable[Any] | None = None,
        language: str = "python",
    ) -> list[str]:
        """Translate policy failures into concrete, task-agnostic repair instructions."""
        kinds = _violation_kinds(violations or [])
        instructions: list[str] = []
        for violation in violations or []:
            refactor = _diagnostic_refactor(violation)
            if refactor:
                instructions.append(f"REPAIR_INSTRUCTION: {refactor}")
        if "parse_error" in kinds:
            instructions.append(
                f"REPAIR_INSTRUCTION: Return syntactically valid {language} source only. Remove markdown fences, prose, partial code, and unresolved placeholders."
            )
        if "cyclomatic_complexity" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Reduce cyclomatic complexity by extracting small single-purpose helper functions. Prefer dictionaries, lookup tables, guard clauses, and simple data mappings over long if/elif chains."
            )
        if "loop_depth" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Reduce nested loop depth. Move inner-loop decisions into helper functions, use generator expressions where behavior stays clear, or precompute simple lookup structures."
            )
        if "global_mutation" in kinds or "module_state_mutation" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Remove global and module-state mutation. Pass state through function arguments and return updated state explicitly; do not use global statements or mutate module-level containers."
            )
        if "external_dependency" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Remove non-standard-library imports. Reimplement the required behavior with Python standard-library modules only."
            )
        if "unknown_api" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Use only APIs listed in the registered library schema. Replace invented or misplaced library calls with the documented namespace path supplied by the engine."
            )
        if "algorithmic_cost" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Remove repeated linear membership checks inside loops. Precompute a set or dictionary lookup before the loop and test against that constant-time structure."
            )
        if "lint_error" in kinds:
            instructions.append(
                "REPAIR_INSTRUCTION: Fix blocking lint errors. Resolve undefined names, invalid imports, impossible attribute access, bad call signatures, and fatal syntax/module errors without adding external dependencies."
            )
        if _behavior_issue_count(behavior_issues or []):
            instructions.append(
                "REPAIR_INSTRUCTION: Preserve behavioral parity. Use the failing input/output cases as tests and do not replace logic with constants or hardcoded shortcuts."
            )
        return instructions

    def decide(
        self,
        source: str,
        violations: Iterable[Any] | None = None,
        behavior_issues: Iterable[Any] | None = None,
        attempt_index: int = 0,
        max_retries: int = 0,
    ) -> RepairDecision:
        violations = list(violations or [])
        behavior_issues = list(behavior_issues or [])
        kinds = _violation_kinds(violations)

        # A draft that does not even parse must first be returned as valid code; a
        # template cannot meaningfully patch unparseable text.
        if "parse_error" in kinds:
            return RepairDecision(
                mode=MODEL_ONLY,
                rationale="Draft failed to parse; request valid code before structural repair.",
                repair_instructions=self.repair_instructions_for(violations, behavior_issues),
            )

        has_issues = bool(violations or behavior_issues)
        if has_issues:
            return RepairDecision(
                mode=MODEL_ONLY,
                rationale="Actionable violations remain; iterate with engine feedback.",
                repair_instructions=self.repair_instructions_for(violations, behavior_issues),
            )

        return RepairDecision(
            mode=MANUAL_REVIEW,
            rationale="No actionable repair path detected for the reported findings.",
        )

    def decide_repeated_failure(
        self,
        violations: Iterable[Any],
        *,
        counterexample: str = "",
        policy_directive: str = "",
    ) -> RepairDecision:
        """Route repeated small-worker failures without guessing unsafe mutations."""
        violations = list(violations or [])
        kinds = _violation_kinds(violations)
        safe = {"external_dependency", "import_risk_block"}
        if kinds and kinds <= safe:
            return RepairDecision(DETERMINISTIC_TRANSFORM, rationale="Known forbidden-import signature.")
        if kinds == {"unsafe_call"} and any(_safe_alternative(item) for item in violations):
            return RepairDecision(DETERMINISTIC_TRANSFORM, rationale="Registered safe API alternative is available.")
        if kinds == {"bounds_risk"} and counterexample.strip() and policy_directive.strip():
            return RepairDecision(DETERMINISTIC_TRANSFORM, rationale="Counterexample-backed bounds directive is available.")
        return RepairDecision(
            JSON_PATCH,
            rationale="Repeated failure requires a typed single-symbol replacement.",
            repair_instructions=[
                'Return one JSON object with target_symbol, action="replace_symbol", and replacement_source.'
            ],
        )


def _safe_alternative(violation: Any) -> str:
    evidence = violation.get("evidence", {}) if isinstance(violation, dict) else getattr(violation, "evidence", {})
    if not isinstance(evidence, dict):
        return ""
    return str(evidence.get("safe_alternative") or evidence.get("registered_safe_alternative") or "")
