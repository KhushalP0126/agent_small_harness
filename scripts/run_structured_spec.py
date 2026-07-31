from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.artifact_manager import ArtifactManager, ArtifactPaths
from agents.config_loader import DEFAULT_CONFIG_PATH, load_config
from agents.generation_controller import GenerationController
from agents.plan_mode import PlanModeAgent
from agents.repair_strategy import RepairStrategyAgent
from backends.architect_client import (
    ArchitectConfig,
    ArchitectModelSupplier,
    ContractArchitectError,
    ContractPlannerSupplier,
)
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaModelSupplier
from harness_kernel.function_contracts import ContractQueue, ContractQueuePlan, DealExample, FunctionContract
from prompt.budget import budget_prompt
from prompt.summarizer import DefaultPromptSummarizer
from validation.import_graph import analyze_import_graph, validate_imported_symbols


DEFAULT_ARTIFACT_ROOT = Path("artifacts/runs")
HELPER_SIGNATURE_RE = re.compile(
    r"`?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\((?P<args>[^`]*)\)\s*(?:->|=>)\s*(?P<returns>[^`]+)`?"
)


@dataclass
class ContractExecutionResult:
    name: str
    status: str
    source: str = ""
    issues: list[dict] = field(default_factory=list)
    prompt_size: int = 0
    dependencies: list[str] = field(default_factory=list)
    repair_attempts: list[dict] = field(default_factory=list)


def _contract_queue_from_architect(
    plan_packet: str,
    plan_context: str,
    profile,
    base_queue: ContractQueue,
) -> tuple[ContractQueue, dict]:
    config = ArchitectConfig(contract_profile=profile)
    metadata = {
        "architect_contracts_requested": True,
        "architect_api_configured": config.api_key_configured,
        "architect_contracts_parsed": False,
        "architect_contract_plan_parsed": False,
        "architect_contract_plan_applied": False,
        "architect_contract_plan": {},
        "architect_contract_plan_raw_response": "",
        "architect_contract_count": 0,
        "architect_contract_error_code": "",
        "architect_contract_error": "",
        "architect_contracts_fallback_used": False,
        "architect_contract_telemetry": [],
    }
    if not config.api_key_configured:
        metadata["architect_contract_error_code"] = "architect_contract_missing_api_key"
        metadata["architect_contract_error"] = "architect API key not configured"
        metadata["architect_contracts_fallback_used"] = bool(base_queue.contracts)
        metadata["architect_contract_count"] = len(base_queue.contracts)
        return base_queue, metadata

    try:
        planner_supplier = ContractPlannerSupplier(profile=profile)
        contract_plan = planner_supplier.build_contract_plan(
            plan_packet=plan_packet,
            preserved_context=plan_context,
            available_contracts=[contract.name for contract in base_queue.contracts],
        )
    except ContractArchitectError as exc:
        metadata["architect_contract_error_code"] = exc.code
        metadata["architect_contract_error"] = str(exc)
        metadata["architect_contracts_fallback_used"] = bool(base_queue.contracts)
        metadata["architect_contract_count"] = len(base_queue.contracts)
        return base_queue, metadata
    except Exception as exc:  # noqa: BLE001 - surfaced in run metadata for review
        metadata["architect_contract_error_code"] = "architect_contract_unexpected_failure"
        metadata["architect_contract_error"] = f"{type(exc).__name__}: {exc}"
        metadata["architect_contracts_fallback_used"] = bool(base_queue.contracts)
        metadata["architect_contract_count"] = len(base_queue.contracts)
        return base_queue, metadata

    queue = _apply_contract_plan(base_queue, contract_plan)
    metadata["architect_contract_plan_parsed"] = True
    metadata["architect_contract_plan_applied"] = True
    metadata["architect_contract_plan"] = asdict(contract_plan)
    metadata["architect_contract_plan_raw_response"] = planner_supplier.last_response
    metadata["architect_contract_count"] = len(queue.contracts)
    metadata["architect_contract_telemetry"] = planner_supplier.telemetry
    return queue, metadata


def _apply_contract_plan(base_queue: ContractQueue, contract_plan: ContractQueuePlan) -> ContractQueue:
    contracts_by_name = {contract.name: contract for contract in base_queue.contracts}
    for name, dependencies in contract_plan.dependencies.items():
        if name not in contracts_by_name:
            continue
        contract = contracts_by_name[name]
        for dependency in dependencies:
            if dependency == name or dependency not in contracts_by_name or dependency in contract.dependencies:
                continue
            contract.dependencies.append(dependency)
    for name, note in contract_plan.contract_notes.items():
        if name not in contracts_by_name or not note:
            continue
        contract = contracts_by_name[name]
        if note not in contract.purpose:
            contract.purpose = f"{contract.purpose}\nArchitect note: {note}".strip()

    requested_names = [name for name in contract_plan.contract_order if name in contracts_by_name]
    remaining_names = [contract.name for contract in base_queue.contracts if contract.name not in requested_names]
    ordered_names = _topological_contract_names([*requested_names, *remaining_names], contracts_by_name)
    return ContractQueue([contracts_by_name[name] for name in ordered_names])


def _contract_queue_payload(queue: ContractQueue) -> list[dict]:
    return [
        {
            "name": contract.name,
            "kind": contract.kind,
            "signature": contract.normalized_signature(),
            "dependencies": contract.dependencies,
            "purpose": contract.purpose,
            "example_count": len(contract.examples),
        }
        for contract in queue.contracts
    ]


def _fallback_contract_queue(plan) -> tuple[ContractQueue, bool]:
    contracts_by_name = _fallback_contracts_from_plan(plan)
    names = _ordered_unique(list(contracts_by_name))
    if not names:
        return ContractQueue(), False
    contracts: list[FunctionContract] = []
    seen: set[str] = set()
    for name in _topological_contract_names(names, contracts_by_name):
        if name in seen:
            continue
        seen.add(name)
        contracts.append(contracts_by_name[name])
    return ContractQueue(contracts), True


def _fallback_contracts_from_plan(plan) -> dict[str, FunctionContract]:
    raw_items = [*plan.components, *plan.entrypoints]
    helper_items = _helper_contract_items(plan.state_machine_constraints)
    contracts: dict[str, FunctionContract] = {}
    known_names = {
        _normalize_symbol(item)
        for item in [*raw_items, *helper_items]
        if _normalize_symbol(item)
    }
    examples_by_name: dict[str, list[DealExample]] = {}
    for case in plan.behavior_cases:
        name = _normalize_symbol(case.call)
        if name:
            examples_by_name.setdefault(name, []).append(DealExample(case.call, case.expected))
            known_names.add(name)

    for item in [*raw_items, *helper_items]:
        contract = _contract_from_spec_item(item)
        if contract.name:
            contracts[contract.name] = contract
    for name, examples in examples_by_name.items():
        contracts.setdefault(
            name,
            FunctionContract(
                name=name,
                signature=f"def {name}(*args, **kwargs)",
                purpose=f"Implement the `{name}` component required by the structured spec.",
            ),
        )
        contracts[name].examples.extend(examples)
    dependencies_by_name = _dependencies_from_graph(plan.dependency_graph_context, known_names)
    for name, contract in contracts.items():
        contract.dependencies.extend(
            dependency
            for dependency in dependencies_by_name.get(name, [])
            if dependency != name and dependency in contracts and dependency not in contract.dependencies
        )
    return contracts


def _helper_contract_items(items: list[str]) -> list[str]:
    return [item for item in items if HELPER_SIGNATURE_RE.search(item)]


def _contract_from_spec_item(item: str) -> FunctionContract:
    value = item.strip().strip("- ").strip()
    helper_match = HELPER_SIGNATURE_RE.search(value)
    if helper_match:
        name = helper_match.group("name")
        args = helper_match.group("args").strip()
        returns = helper_match.group("returns").strip().strip(".")
        return FunctionContract(
            name=name,
            signature=f"def {name}({args}) -> {returns}",
            purpose=f"Implement the `{name}` component required by the structured spec.",
        )
    name = _normalize_symbol(value)
    kind = "class" if name[:1].isupper() else "function"
    signature = f"class {name}:" if kind == "class" else f"def {name}(*args, **kwargs)"
    return FunctionContract(
        name=name,
        kind=kind,
        signature=signature,
        purpose=f"Implement the `{name}` component required by the structured spec.",
    )


def _dependencies_from_graph(graph_lines: list[str], known_names: set[str]) -> dict[str, list[str]]:
    dependencies: dict[str, list[str]] = {name: [] for name in known_names}
    for line in graph_lines:
        symbols = [
            token
            for token in re.findall(r"`?([A-Za-z_][A-Za-z0-9_]*)`?", line)
            if token in known_names
        ]
        for left, right in zip(symbols, symbols[1:]):
            if left == right:
                continue
            dependencies.setdefault(right, [])
            if left not in dependencies[right]:
                dependencies[right].append(left)
    return dependencies


def _topological_contract_names(names: list[str], contracts: dict[str, FunctionContract]) -> list[str]:
    ordered: list[str] = []
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(name: str) -> None:
        if name in permanent or name not in contracts:
            return
        if name in temporary:
            return
        temporary.add(name)
        for dependency in contracts[name].dependencies:
            visit(dependency)
        temporary.remove(name)
        permanent.add(name)
        ordered.append(name)

    for name in names:
        visit(name)
    return ordered


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _initial_prompt(plan_packet: str, queue: ContractQueue) -> str:
    sections = [
        "You are a Python coding worker inside a verified Plan-Execute-Verify harness.",
        "Return only complete Python code.",
        "",
        plan_packet,
    ]
    if queue.contracts:
        sections.extend(
            [
                "",
                "ARCHITECT FUNCTION CONTRACT QUEUE:",
                "Implement the requested program using these function contracts as the build order.",
                "For pure helper functions, include the Deal examples as decorators so the harness can execute them.",
                "Do not decorate UI or infinite-loop entrypoints.",
                "",
                queue.to_deal_scaffold(),
                "",
                "FUNCTIONWISE WORKER PACKETS:",
                *queue.to_worker_packets(),
            ]
        )
    sections.extend(
        [
            "",
            "FINAL RULES:",
            "- Generated code must parse as Python.",
            "- Generated code must pass all static engines.",
            "- If Deal decorators are included, their examples must pass.",
            "- Keep app-specific requirements in the provided spec; do not invent extra features.",
        ]
    )
    return "\n".join(sections)


def _symbol_terms(contract: FunctionContract, dependencies: list[str]) -> set[str]:
    terms = {contract.name, *dependencies}
    for text in [
        contract.signature,
        contract.purpose,
        contract.output,
        *contract.inputs,
        *contract.invariants,
        *(f"{example.call} {example.expected}" for example in contract.examples),
    ]:
        terms.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
    return {term.lower() for term in terms if len(term) > 2}


def _local_graph_context(plan, contract: FunctionContract, dependencies: list[str]) -> str:
    terms = _symbol_terms(contract, dependencies)

    def matching(lines: list[str]) -> list[str]:
        matched = []
        for line in lines:
            lowered = line.lower()
            if any(term in lowered for term in terms):
                matched.append(line)
        return matched

    graph_lines = matching(plan.dependency_graph_context)
    state_lines = matching(plan.state_machine_constraints)
    behavior_lines = [
        f"{case.call} == {case.expected}"
        for case in plan.behavior_cases
        if any(term in f"{case.call} {case.expected}".lower() for term in terms)
    ]

    sections = [
        "LOCAL GRAPH CONTEXT:",
        f"- Language: {plan.language or 'python'}",
        f"- Task type: {plan.task_type or 'code'}",
    ]
    if plan.app_name:
        sections.append(f"- App: {plan.app_name}")
    if plan.allowed_libraries:
        sections.append(f"- Allowed libraries: {', '.join(plan.allowed_libraries)}")
    sections.extend(
        [
            f"- Current contract: {contract.name}",
            f"- Contract kind: {contract.kind}",
            f"- Direct dependencies: {', '.join(dependencies) if dependencies else 'none'}",
        ]
    )
    if graph_lines:
        sections.append("Dependency graph slice:")
        sections.extend(f"- {line}" for line in graph_lines)
    if state_lines:
        sections.append("State/rule slice:")
        sections.extend(f"- {line}" for line in state_lines[:8])
    if behavior_lines:
        sections.append("Behavior examples for this contract:")
        sections.extend(f"- {line}" for line in behavior_lines)
    if plan.performance_constraints:
        sections.append("Performance constraints:")
        sections.extend(f"- {line}" for line in plan.performance_constraints)
    if plan.security_constraints:
        sections.append("Safety constraints:")
        sections.extend(f"- {line}" for line in plan.security_constraints)
    return "\n".join(sections)


def _expression_type(expression: ast.expr) -> str:
    if isinstance(expression, ast.Tuple):
        item_types = [_expression_type(item) for item in expression.elts]
        return f"tuple[{', '.join(item_types)}] (immutable)"
    if isinstance(expression, ast.List):
        item_type = _expression_type(expression.elts[0]) if expression.elts else "unknown"
        return f"list[{item_type}] (mutable)"
    if isinstance(expression, ast.Dict):
        return "dict (mutable)"
    if isinstance(expression, ast.Set):
        return "set (mutable)"
    if isinstance(expression, ast.Constant):
        return type(expression.value).__name__
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.operand, ast.Constant):
        return type(expression.operand.value).__name__
    if isinstance(expression, ast.Call):
        if isinstance(expression.func, ast.Name):
            return expression.func.id
        if isinstance(expression.func, ast.Attribute):
            return expression.func.attr
    return "unknown"


def _method_contract(class_name: str, method: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    positional_args = [*method.args.posonlyargs, *method.args.args]
    decorators = {
        decorator.id
        for decorator in method.decorator_list
        if isinstance(decorator, ast.Name)
    }
    if "staticmethod" not in decorators and positional_args and positional_args[0].arg in {"self", "cls"}:
        positional_args = positional_args[1:]

    positional_names = [argument.arg for argument in positional_args]
    rendered_parameters = list(positional_names)
    if method.args.vararg is not None:
        rendered_parameters.append(f"*{method.args.vararg.arg}")
    elif method.args.kwonlyargs:
        rendered_parameters.append("*")
    rendered_parameters.extend(argument.arg for argument in method.args.kwonlyargs)
    if method.args.kwarg is not None:
        rendered_parameters.append(f"**{method.args.kwarg.arg}")

    default_count = min(len(method.args.defaults), len(positional_args))
    required_count = len(positional_args) - default_count
    if method.args.vararg is not None:
        arity = f"at least {required_count} positional"
    elif required_count == len(positional_args):
        arity = f"exactly {required_count} positional"
    else:
        arity = f"{required_count} to {len(positional_args)} positional"
    return_type = ast.unparse(method.returns) if method.returns is not None else "unknown"
    return (
        f"{class_name}.{method.name}({', '.join(rendered_parameters)}) -> {return_type}; "
        f"call arity: {arity} (excluding self/cls)"
    )


def _accepted_type_context(accepted_sources: list[str]) -> list[str]:
    """Extract field and callable commitments from accepted class contracts."""

    commitments: dict[str, str] = {}
    for source in accepted_sources:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for node in ast.walk(class_node):
                if isinstance(node, ast.AnnAssign):
                    if isinstance(node.target, ast.Name):
                        name = f"{class_node.name}.{node.target.id}"
                        commitments[name] = f"{name}: {ast.unparse(node.annotation)}"
                    elif (
                        isinstance(node.target, ast.Attribute)
                        and isinstance(node.target.value, ast.Name)
                        and node.target.value.id == "self"
                    ):
                        name = f"{class_node.name}.{node.target.attr}"
                        commitments[name] = f"{name}: {ast.unparse(node.annotation)}"
                elif isinstance(node, ast.Assign):
                    inferred = _expression_type(node.value)
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                        ):
                            name = f"{class_node.name}.{target.attr}"
                            commitments.setdefault(name, f"{name}: {inferred}")
            for method in (
                node
                for node in class_node.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ):
                name = f"{class_node.name}.{method.name}"
                commitments[name] = _method_contract(class_node.name, method)
    return [commitments[name] for name in sorted(commitments)]


def _single_contract_prompt(
    plan,
    contract: FunctionContract,
    dependency_sources: list[str],
    dependencies: list[str],
    accepted_type_context: list[str] | None = None,
) -> str:
    sections = [
        "You are a Python coding worker inside a verified Plan-Execute-Verify harness.",
        "Implement exactly one function contract.",
        "Return only complete Python code for this function and any tiny local helpers it needs.",
        "Do not return markdown fences or prose.",
        "",
        _local_graph_context(plan, contract, dependencies),
        "",
        contract.to_worker_packet(),
    ]
    if dependency_sources:
        sections.extend(
            [
                "",
                "ACCEPTED DIRECT DEPENDENCIES:",
                "\n\n".join(dependency_sources),
                "",
                "Use these accepted dependencies only when needed. Do not rewrite them.",
            ]
        )
    if accepted_type_context:
        sections.extend(
            [
                "",
                "ACCEPTED TYPE CONTRACTS:",
                *[f"- {item}" for item in accepted_type_context],
                "- Preserve these representations. Values marked immutable must be replaced, not item-mutated.",
                "- Accepted method signatures and call arities are binding; do not add or omit call arguments.",
            ]
        )
    sections.extend(
        [
            "",
            "VALIDATION:",
            "- The harness will parse this function immediately.",
            "- The harness will run the listed Deal examples immediately.",
            "- If this function fails, the queue stops before later contracts run.",
        ]
    )
    return "\n".join(sections)


def _contract_repair_prompt(
    plan,
    contract: FunctionContract,
    current_source: str,
    issues: list[dict],
    dependency_sources: list[str],
    dependencies: list[str],
    worker_name: str,
    accepted_type_context: list[str] | None = None,
    prompt_summarizer: Callable[[str], str] | None = None,
) -> str:
    sections = [
        "FUNCTION CONTRACT REPAIR",
        "",
        f"Worker: {worker_name}",
        "Repair only the failed contract below.",
        "Return only complete Python code for this one contract and required tiny local helpers.",
        "Do not return markdown fences or prose.",
        "",
        _local_graph_context(plan, contract, dependencies),
        "",
        contract.to_worker_packet(),
        "",
        "CURRENT FAILED SOURCE:",
        current_source,
        "",
        "VALIDATION FAILURES:",
        json.dumps(issues, indent=2),
    ]
    if dependency_sources:
        sections.extend(
            [
                "",
                "ACCEPTED DIRECT DEPENDENCIES:",
                "\n\n".join(dependency_sources),
                "",
                "Use these accepted dependencies. Do not rewrite them.",
            ]
        )
    if accepted_type_context:
        sections.extend(
            [
                "",
                "ACCEPTED TYPE CONTRACTS:",
                *[f"- {item}" for item in accepted_type_context],
                "- Preserve these representations. Values marked immutable must be replaced, not item-mutated.",
                "- Accepted method signatures and call arities are binding; do not add or omit call arguments.",
            ]
        )
    sections.extend(
        [
            "",
            "REPAIR RULES:",
            "- Fix the listed validation failures directly.",
            "- Preserve the contract signature and examples.",
            "- Keep the implementation small enough for parse/static checks.",
            "- If the source is truncated, return a complete replacement for this contract.",
        ]
    )
    return budget_prompt(
        "\n".join(sections),
        summarizer=prompt_summarizer,
    ).text


def _contract_dependencies(contract: FunctionContract, known_names: set[str]) -> list[str]:
    dependencies: list[str] = []
    dependency_texts = list(contract.dependencies)
    dependency_texts.append(contract.signature)
    dependency_texts.extend(f"{example.call} == {example.expected}" for example in contract.examples)
    dependency_texts.extend(contract.inputs)
    if contract.output:
        dependency_texts.append(contract.output)
    dependency_texts.extend(contract.invariants)
    for item in dependency_texts:
        normalized = _normalize_symbol(item)
        if normalized in known_names and normalized != contract.name:
            dependencies.append(normalized)
            continue
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", item):
            if token in known_names and token != contract.name:
                dependencies.append(token)
    return sorted(set(dependencies), key=dependencies.index)


def _validate_contract_source(
    source: str,
    contract: FunctionContract,
    accepted_sources: list[str] | None = None,
    external_roots: set[str] | None = None,
) -> list[dict]:
    issues: list[dict] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [
            {
                "kind": "contract_parse_error",
                "summary": f"Contract `{contract.name}` source does not parse",
                "details": str(exc),
            }
        ]
    if contract.kind == "class":
        has_required_symbol = any(isinstance(node, ast.ClassDef) and node.name == contract.name for node in ast.walk(tree))
    else:
        has_required_symbol = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == contract.name for node in ast.walk(tree)
        )
    if not has_required_symbol:
        issues.append(
            {
                "kind": "contract_missing_function",
                "summary": f"Contract `{contract.name}` did not define the required symbol",
                "details": contract.normalized_signature(),
            }
        )
    for missing_symbol in validate_imported_symbols(source, external_roots=external_roots):
        issues.append(
            {
                "kind": "contract_missing_import_symbol",
                "summary": f"Contract `{contract.name}` imports a symbol that does not exist",
                "details": missing_symbol,
            }
        )
    if not contract.examples:
        return issues
    namespace: dict[str, object] = {}
    try:
        for accepted_source in accepted_sources or []:
            exec(accepted_source, namespace)  # noqa: S102 - generated code is already executed by harness validators.
        exec(source, namespace)  # noqa: S102 - generated code is already executed by harness validators.
    except Exception as exc:  # noqa: BLE001 - reported as validation evidence
        issues.append(
            {
                "kind": "contract_execution_error",
                "summary": f"Contract `{contract.name}` crashed during example setup",
                "details": f"{type(exc).__name__}: {exc}",
            }
        )
        return issues
    for example in contract.examples:
        expression = f"{example.call} == {example.expected}"
        try:
            passed = bool(eval(expression, namespace))  # noqa: S307 - concrete contract examples are test code.
        except Exception as exc:  # noqa: BLE001 - reported as validation evidence
            issues.append(
                {
                    "kind": "contract_example_error",
                    "summary": f"Contract `{contract.name}` example crashed",
                    "details": f"{expression}: {type(exc).__name__}: {exc}",
                }
            )
            continue
        if not passed:
            issues.append(
                {
                    "kind": "contract_example_failed",
                    "summary": f"Contract `{contract.name}` example failed",
                    "details": expression,
                }
            )
    return issues


def _run_contract_queue_sequentially(
    queue: ContractQueue,
    plan,
    generate_draft: Callable[[str], str],
    repair_draft: Callable[[str, str], str] | None = None,
    architect_repair_draft: Callable[[str, str], str] | None = None,
    small_retries_per_contract: int = 1,
    architect_retries_per_contract: int = 1,
    prompt_summarizer: Callable[[str], str] | None = None,
    resume_results: list[ContractExecutionResult] | None = None,
    checkpoint_writer: Callable[[list[str], list[ContractExecutionResult]], None] | None = None,
) -> tuple[list[str], list[ContractExecutionResult]]:
    results: list[ContractExecutionResult] = list(resume_results or [])
    accepted_results = [
        result
        for result in results
        if result.status == "accepted" and result.source
    ]
    accepted_sources: list[str] = [result.source for result in accepted_results]
    accepted_source_by_name: dict[str, str] = {
        result.name: result.source for result in accepted_results
    }
    accepted_names: set[str] = set(accepted_source_by_name)
    completed_names = {
        result.name
        for result in results
        if result.name != "<queue>"
    }
    total = len(queue.contracts)
    known_names = {contract.name for contract in queue.contracts}
    ready_queue = [
        contract
        for contract in queue.contracts
        if contract.name not in completed_names
    ]
    blocked_stack: list[FunctionContract] = []
    generated_count = len(completed_names)

    def write_checkpoint() -> None:
        if checkpoint_writer is not None:
            checkpoint_writer(accepted_sources, results)

    while ready_queue or blocked_stack:
        if not ready_queue and blocked_stack:
            remaining = [
                (contract.name, _contract_dependencies(contract, known_names))
                for contract in blocked_stack
            ]
            progressable = [
                contract
                for contract in reversed(blocked_stack)
                if all(dep in accepted_names for dep in _contract_dependencies(contract, known_names))
            ]
            if not progressable:
                names = ", ".join(
                    f"{name} waiting on {', '.join(dep for dep in deps if dep not in accepted_names) or 'unknown'}"
                    for name, deps in remaining
                )
                print(f"[contract-stack] blocked: {names}", flush=True)
                results.append(
                    ContractExecutionResult(
                        name="<queue>",
                        status="dependency_blocked",
                        issues=[
                            {
                                "kind": "contract_dependency_blocked",
                                "summary": "Contract queue could not make progress",
                                "details": names,
                            }
                        ],
                    )
                )
                write_checkpoint()
                break
            ready_queue.extend(progressable)
            blocked_stack = [contract for contract in blocked_stack if contract not in progressable]

        contract = ready_queue.pop(0)
        dependencies = _contract_dependencies(contract, known_names)
        unmet = [dependency for dependency in dependencies if dependency not in accepted_names]
        if unmet:
            print(
                f"[contract-queue] {contract.name}: waiting on {', '.join(unmet)}; pushing to dependency stack",
                flush=True,
            )
            blocked_stack.append(contract)
            continue

        generated_count += 1
        dependency_sources = [accepted_source_by_name[name] for name in dependencies if name in accepted_source_by_name]
        accepted_type_context = _accepted_type_context(accepted_sources)
        prompt = _single_contract_prompt(
            plan,
            contract,
            dependency_sources,
            dependencies,
            accepted_type_context=accepted_type_context,
        )
        print(
            f"[contract-queue] {generated_count}/{total} {contract.name}: sending to small worker",
            flush=True,
        )
        repair_attempts: list[dict] = []
        try:
            source = generate_draft(prompt)
        except Exception as exc:  # noqa: BLE001 - surfaced in artifact metadata
            print(
                f"[contract-queue] {generated_count}/{total} {contract.name}: backend failed: {type(exc).__name__}: {exc}",
                flush=True,
            )
            results.append(
                ContractExecutionResult(
                    name=contract.name,
                    status="backend_failed",
                    issues=[
                        {
                            "kind": "contract_backend_failure",
                            "summary": f"Small worker failed while implementing `{contract.name}`",
                            "details": f"{type(exc).__name__}: {exc}",
                        }
                    ],
                    prompt_size=len(prompt),
                    dependencies=dependencies,
                )
            )
            write_checkpoint()
            continue
        allowed_libraries = set(getattr(plan, "allowed_libraries", []))
        issues = _validate_contract_source(
            source,
            contract,
            accepted_sources=accepted_sources,
            external_roots=allowed_libraries,
        )
        small_repair = repair_draft or (lambda draft, retry_prompt: generate_draft(retry_prompt))
        for retry_index in range(small_retries_per_contract):
            if not issues:
                break
            retry_prompt = _contract_repair_prompt(
                plan,
                contract,
                source,
                issues,
                dependency_sources,
                dependencies,
                worker_name="small_worker",
                accepted_type_context=accepted_type_context,
                prompt_summarizer=prompt_summarizer,
            )
            print(
                f"[contract-queue] {generated_count}/{total} {contract.name}: retry {retry_index + 1} with small worker",
                flush=True,
            )
            try:
                repaired_source = small_repair(source, retry_prompt)
            except Exception as exc:  # noqa: BLE001 - surfaced in artifact metadata
                repair_attempts.append(
                    {
                        "worker": "small_worker",
                        "status": "backend_failed",
                        "issues": [
                            {
                                "kind": "contract_backend_failure",
                                "summary": f"Small worker failed while repairing `{contract.name}`",
                                "details": f"{type(exc).__name__}: {exc}",
                            }
                        ],
                        "prompt_size": len(retry_prompt),
                    }
                )
                break
            source = repaired_source
            issues = _validate_contract_source(
                source,
                contract,
                accepted_sources=accepted_sources,
                external_roots=allowed_libraries,
            )
            repair_attempts.append(
                {
                    "worker": "small_worker",
                    "status": "accepted" if not issues else "validation_failed",
                    "issues": issues,
                    "prompt_size": len(retry_prompt),
                }
            )
        for retry_index in range(architect_retries_per_contract):
            if not issues or architect_repair_draft is None:
                break
            retry_prompt = _contract_repair_prompt(
                plan,
                contract,
                source,
                issues,
                dependency_sources,
                dependencies,
                worker_name="architect_llm",
                accepted_type_context=accepted_type_context,
                prompt_summarizer=prompt_summarizer,
            )
            print(
                f"[contract-queue] {generated_count}/{total} {contract.name}: escalating contract to architect",
                flush=True,
            )
            try:
                architect_source = architect_repair_draft(source, retry_prompt)
            except Exception as exc:  # noqa: BLE001 - surfaced in artifact metadata
                repair_attempts.append(
                    {
                        "worker": "architect_llm",
                        "status": "backend_failed",
                        "issues": [
                            {
                                "kind": "contract_backend_failure",
                                "summary": f"Architect failed while repairing `{contract.name}`",
                                "details": f"{type(exc).__name__}: {exc}",
                            }
                        ],
                        "prompt_size": len(retry_prompt),
                    }
                )
                break
            source = architect_source
            issues = _validate_contract_source(
                source,
                contract,
                accepted_sources=accepted_sources,
                external_roots=allowed_libraries,
            )
            repair_attempts.append(
                {
                    "worker": "architect_llm",
                    "status": "accepted" if not issues else "validation_failed",
                    "issues": issues,
                    "prompt_size": len(retry_prompt),
                }
            )
        if issues:
            print(f"[contract-queue] {generated_count}/{total} {contract.name}: failed validation", flush=True)
            results.append(
                ContractExecutionResult(
                    name=contract.name,
                    status="validation_failed",
                    source=source,
                    issues=issues,
                    prompt_size=len(prompt),
                    dependencies=dependencies,
                    repair_attempts=repair_attempts,
                )
            )
            write_checkpoint()
            continue
        print(f"[contract-queue] {generated_count}/{total} {contract.name}: accepted", flush=True)
        accepted_sources.append(source)
        accepted_source_by_name[contract.name] = source
        accepted_names.add(contract.name)
        results.append(
            ContractExecutionResult(
                name=contract.name,
                status="accepted",
                source=source,
                prompt_size=len(prompt),
                dependencies=dependencies,
                repair_attempts=repair_attempts,
            )
        )
        write_checkpoint()
    return accepted_sources, results


def _integration_prompt(
    plan_packet: str,
    accepted_sources: list[str],
    results: list[ContractExecutionResult],
    prompt_summarizer: Callable[[str], str] | None = None,
) -> str:
    return budget_prompt("\n".join(
        [
            "FUNCTIONWISE CONTRACT INTEGRATION",
            "",
            "The small worker implemented function contracts sequentially.",
            "Build the final complete Python module from the accepted functions and the structured spec.",
            "Return code only. Do not return prose or markdown fences.",
            "",
            "PLAN PACKET:",
            plan_packet,
            "",
            "CONTRACT QUEUE RESULTS:",
            json.dumps([asdict(result) for result in results], indent=2),
            "",
            "ACCEPTED FUNCTION SOURCES:",
            "\n\n".join(accepted_sources) or "(none)",
            "",
            "FINAL INTEGRATION RULES:",
            "- Preserve accepted helper behavior.",
            "- Add any required classes, adapters, entrypoints, and glue code from the spec.",
            "- Do not drop, rename, or omit required components or entrypoints from the structured spec.",
            "- The final module must define every required symbol even when a helper is implemented differently internally.",
            "- Do not use file I/O, network calls, eval, or exec.",
            "- Do not use wildcard imports. Import modules and qualify their names, or import required symbols explicitly.",
            "- Keep the main loop guarded by if __name__ == \"__main__\" when an app entrypoint is required.",
            "- The final code will be scanned by all engines and formal/Deal gates.",
        ]
    ), summarizer=prompt_summarizer).text


def _normalize_symbol(text: str) -> str:
    value = text.strip().strip("`")
    if "(" in value:
        value = value.split("(", 1)[0]
    return value.strip()


def _validate_structured_spec_output(source: str, plan) -> list[dict]:
    issues: list[dict] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [
            {
                "kind": "spec_parse_error",
                "summary": "Generated source does not parse for spec validation",
                "details": str(exc),
            }
        ]
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)

    for component in plan.components:
        symbol = _normalize_symbol(component)
        if symbol and symbol not in defined:
            issues.append(
                {
                    "kind": "missing_component",
                    "summary": f"Required component `{symbol}` is missing",
                    "details": component,
                }
            )
    for entrypoint in plan.entrypoints:
        symbol = _normalize_symbol(entrypoint)
        if symbol and symbol not in defined:
            issues.append(
                {
                    "kind": "missing_entrypoint",
                    "summary": f"Required entrypoint `{symbol}` is missing",
                    "details": entrypoint,
                }
            )
    file_map = _structured_spec_file_map(source, plan)
    import_graph = analyze_import_graph(file_map, external_roots=set(getattr(plan, "allowed_libraries", [])))
    for path, missing in import_graph.missing_imports.items():
        issues.append(
            {
                "kind": "missing_local_import",
                "summary": f"Generated file `{path}` imports missing local modules",
                "details": ", ".join(missing),
            }
        )
    for path, missing in import_graph.missing_symbols.items():
        issues.append(
            {
                "kind": "missing_import_symbol",
                "summary": f"Generated file `{path}` imports symbols that do not exist",
                "details": ", ".join(missing),
            }
        )
    return issues


def _structured_spec_file_map(source: str, plan) -> dict[str, str]:
    files = [path for path in getattr(plan, "files", []) if str(path).endswith(".py")]
    if not files:
        return {"generated_source.py": source}
    return {files[0]: source, **{path: "" for path in files[1:]}}


def _run_integration_smoke_test(source: str, plan, timeout_seconds: float = 5.0) -> dict:
    """Start the assembled Python entrypoint and reject immediate runtime crashes.

    Interactive applications are expected to keep running. Surviving the bounded
    startup window is therefore a pass; the subprocess is killed by the timeout.
    Programs that exit cleanly are also accepted.
    """

    if not source or str(getattr(plan, "language", "python")).lower() != "python":
        return {"is_compliant": True, "status": "skipped", "issues": []}
    if not getattr(plan, "entrypoints", []):
        return {"is_compliant": True, "status": "skipped_no_entrypoint", "issues": []}

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", encoding="utf-8", delete=False) as handle:
            handle.write(source)
            temp_path = Path(handle.name)
        env = os.environ.copy()
        env.update(
            {
                "SDL_VIDEODRIVER": "dummy",
                "SDL_AUDIODRIVER": "dummy",
                "PYTHONDONTWRITEBYTECODE": "1",
                "HARNESS_SMOKE_TEST": "1",
            }
        )
        try:
            completed = subprocess.run(
                [sys.executable, str(temp_path)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {
                "is_compliant": True,
                "status": "running_after_smoke_window",
                "timeout_seconds": timeout_seconds,
                "issues": [],
            }
        if completed.returncode == 0:
            return {
                "is_compliant": True,
                "status": "exited_cleanly",
                "returncode": completed.returncode,
                "issues": [],
            }
        details = (completed.stderr or completed.stdout).strip()[-4000:]
        return {
            "is_compliant": False,
            "status": "crashed",
            "returncode": completed.returncode,
            "issues": [
                {
                    "kind": "integration_smoke_crash",
                    "summary": "Generated program crashed during integration smoke execution",
                    "details": details or f"process exited with status {completed.returncode}",
                }
            ],
        }
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def run_spec(
    spec_path: Path,
    model: str,
    artifact_root: Path,
    save_artifacts: bool,
    max_retries: int,
    architect_after_repair_attempts: int | None,
    use_architect_contracts: bool,
    config_path: Path,
    plan_only: bool = False,
    prompt_summarizer: Callable[[str], str] | None = None,
    resume_run_id: str | None = None,
) -> int:
    if plan_only and resume_run_id:
        raise ValueError("--resume-run cannot be combined with --plan-only")
    config = load_config(config_path)
    active_prompt_summarizer = prompt_summarizer or DefaultPromptSummarizer()
    spec_text = spec_path.read_text(encoding="utf-8")
    manager = ArtifactManager(artifact_root)
    paths: ArtifactPaths | None = None
    resume_results: list[ContractExecutionResult] = []
    if resume_run_id:
        checkpoint = manager.load_checkpoint(resume_run_id)
        if checkpoint is None:
            raise ValueError(
                f"No checkpoint found for run '{resume_run_id}' under {artifact_root}"
            )
        if checkpoint.get("kind") != "structured_spec":
            raise ValueError(
                f"Checkpoint '{resume_run_id}' is not a structured-spec checkpoint"
            )
        if checkpoint.get("spec_path") != str(spec_path):
            raise ValueError(
                f"Checkpoint '{resume_run_id}' belongs to {checkpoint.get('spec_path')}, "
                f"not {spec_path}"
            )
        paths = ArtifactPaths(
            run_id=resume_run_id,
            run_dir=artifact_root / resume_run_id,
        )
        resume_results = [
            ContractExecutionResult(**item)
            for item in checkpoint.get("contract_results", [])
        ]
    elif save_artifacts and not plan_only:
        paths = manager.create_run(prefix=f"structured_spec_{spec_path.stem}")
    plan_mode = PlanModeAgent()
    plan = plan_mode.plan(spec_text)
    plan_packet = plan_mode.to_worker_packet(plan)
    plan_context = plan_mode.to_prompt_context(plan)
    queue = ContractQueue()
    contract_metadata = {
        "architect_contracts_requested": False,
        "architect_api_configured": ArchitectConfig().api_key_configured,
        "architect_contracts_parsed": False,
        "architect_contract_plan_parsed": False,
        "architect_contract_plan_applied": False,
        "architect_contract_plan": {},
        "architect_contract_plan_raw_response": "",
        "architect_contract_count": 0,
        "architect_contract_error_code": "",
        "architect_contract_error": "",
        "architect_contracts_fallback_used": False,
    }
    if use_architect_contracts:
        fallback_queue, fallback_used = _fallback_contract_queue(plan)
        queue, contract_metadata = _contract_queue_from_architect(
            plan_packet,
            plan_context,
            config.execution.architect.contract,
            fallback_queue,
        )
        if not queue.contracts:
            if fallback_used:
                queue = fallback_queue
                contract_metadata["architect_contracts_fallback_used"] = True
                contract_metadata["architect_contract_count"] = len(queue.contracts)
            elif plan.components:
                payload = {
                    "status": "manual_review_required",
                    "reason": "missing_contract_queue_for_structured_spec",
                    "architect_contracts_requested": contract_metadata["architect_contracts_requested"],
                    "architect_api_configured": contract_metadata["architect_api_configured"],
                    "architect_contract_error_code": contract_metadata["architect_contract_error_code"],
                    "architect_contract_error": contract_metadata["architect_contract_error"],
                    "artifact_path": "",
                }
                print(json.dumps(payload, indent=2))
                return 1
        print(
            "[contract-plan] "
            + json.dumps(
                {"contracts": _contract_queue_payload(queue)},
                separators=(",", ":"),
            ),
            flush=True,
        )
    if plan_only:
        artifact_path = ""
        session = {
            "target": spec_text,
            "route": "structured_spec_contract_plan",
            "max_retries": max_retries,
            "attempts": [],
            "final_status": "planned",
        }
        metadata = {
            "spec_path": str(spec_path),
            "model": model,
            "plan": {
                "task_type": plan.task_type,
                "language": plan.language,
                "app_name": plan.app_name,
                "game_kind": plan.game_kind,
                "route_hint": plan.route_hint,
                "allowed_libraries": plan.allowed_libraries,
                "state_rules": plan.state_machine_constraints,
                "dependency_graph": plan.dependency_graph_context,
            },
            "contract_queue": {
                **contract_metadata,
                "contracts": _contract_queue_payload(queue),
                "worker_packets": queue.to_worker_packets(),
            },
        }
        if save_artifacts:
            paths = manager.create_run(prefix=f"structured_spec_plan_{spec_path.stem}")
            artifact_path = str(paths.run_dir)
            manager.save_session(session, paths, metadata=metadata)
        print(
            json.dumps(
                {
                    "status": "planned",
                    "architect_contracts_requested": contract_metadata["architect_contracts_requested"],
                    "architect_api_configured": contract_metadata["architect_api_configured"],
                    "architect_contract_plan_parsed": contract_metadata["architect_contract_plan_parsed"],
                    "architect_contract_plan_applied": contract_metadata["architect_contract_plan_applied"],
                    "architect_contract_plan": contract_metadata["architect_contract_plan"],
                    "architect_contract_plan_raw_response": contract_metadata["architect_contract_plan_raw_response"],
                    "architect_contracts_fallback_used": contract_metadata["architect_contracts_fallback_used"],
                    "architect_contract_error": contract_metadata["architect_contract_error"],
                    "contract_count": len(queue.contracts),
                    "contracts": _contract_queue_payload(queue),
                    "artifact_path": artifact_path,
                },
                indent=2,
            )
        )
        return 0

    supplier = OllamaModelSupplier(model=model)
    architect_model_supplier = ArchitectModelSupplier(config=ArchitectConfig(repair_profile=config.execution.architect.repair))
    architect_supplier = (
        architect_model_supplier.repair_draft if architect_after_repair_attempts is not None else None
    )
    controller = GenerationController(
        max_retries=max_retries,
        draft_supplier=supplier.generate_draft,
        repair_supplier=supplier.repair_draft,
        architect_supplier=architect_supplier,
        architect_after_repair_attempts=architect_after_repair_attempts,
        policy=config.engines.policy.to_validation_policy(),
        behavior_spec=None,
        crosshair_enabled=config.engines.formal.crosshair_enabled,
        crosshair_timeout_seconds=config.engines.formal.crosshair_timeout_seconds,
        repair_strategy=RepairStrategyAgent(),
        prompt_summarizer=active_prompt_summarizer,
    )
    contract_execution_results: list[ContractExecutionResult] = []
    if queue.contracts:
        def checkpoint_contract_queue(
            accepted_sources: list[str],
            results: list[ContractExecutionResult],
        ) -> None:
            if paths is None:
                return
            manager.checkpoint(
                {
                    "version": 1,
                    "kind": "structured_spec",
                    "phase": "contract_queue",
                    "spec_path": str(spec_path),
                    "model": model,
                    "accepted_sources": accepted_sources,
                    "contract_results": [asdict(result) for result in results],
                },
                paths,
            )

        accepted_sources, contract_execution_results = _run_contract_queue_sequentially(
            queue,
            plan,
            supplier.generate_draft,
            repair_draft=supplier.repair_draft,
            architect_repair_draft=architect_supplier,
            small_retries_per_contract=max_retries,
            architect_retries_per_contract=1 if architect_supplier is not None else 0,
            prompt_summarizer=active_prompt_summarizer,
            resume_results=resume_results,
            checkpoint_writer=checkpoint_contract_queue,
        )
        if not accepted_sources:
            session = {
                "target": spec_text,
                "route": "function_contract_queue",
                "max_retries": max_retries,
                "attempts": [],
                "final_status": "manual_review_required",
                "human_review": {
                    "status": "manual_review_required",
                    "reason": "function_contract_queue_failed",
                    "blocking_findings": [],
                    "blocking_violations": [
                        issue
                        for result in contract_execution_results
                        for issue in result.issues
                    ],
                    "behavior_issues": [],
                    "formal_issues": [],
                    "last_retry_prompt": "",
                    "diagnostic_deltas": [],
                    "repair_directives": [],
                    "suggested_human_decision": (
                        "Review the failed function contract or reduce the contract before continuing the queue."
                    ),
                },
            }
        else:
            integration_prompt = _integration_prompt(
                plan_packet,
                accepted_sources,
                contract_execution_results,
                prompt_summarizer=active_prompt_summarizer,
            )
            print("[contract-queue] all contracts accepted; sending accepted functions to architect integrator", flush=True)
            try:
                integrated_source = architect_model_supplier.repair_draft("", integration_prompt)
            except Exception as exc:  # noqa: BLE001 - surfaced in run output and metadata
                session = {
                    "target": spec_text,
                    "route": "function_contract_queue",
                    "max_retries": max_retries,
                    "attempts": [],
                    "final_status": "manual_review_required",
                    "human_review": {
                        "status": "manual_review_required",
                        "reason": "architect_contract_integration_failed",
                        "blocking_findings": [],
                        "blocking_violations": [
                            {
                                "kind": "architect_contract_integration_failed",
                                "summary": "Architect failed while integrating accepted function contracts",
                                "details": f"{type(exc).__name__}: {exc}",
                            }
                        ],
                        "behavior_issues": [],
                        "formal_issues": [],
                        "last_retry_prompt": integration_prompt,
                        "diagnostic_deltas": [],
                        "repair_directives": [],
                        "suggested_human_decision": (
                            "Retry architect integration or inspect the accepted function snippets manually."
                        ),
                    },
                }
            else:
                integration_controller = GenerationController(
                    max_retries=max_retries,
                    draft_supplier=supplier.generate_draft,
                    repair_supplier=supplier.repair_draft,
                    architect_supplier=architect_supplier,
                    architect_after_repair_attempts=0 if architect_supplier is not None else None,
                    policy=config.engines.policy.to_validation_policy(),
                    behavior_spec=None,
                    crosshair_enabled=config.engines.formal.crosshair_enabled,
                    crosshair_timeout_seconds=config.engines.formal.crosshair_timeout_seconds,
                    repair_strategy=RepairStrategyAgent(),
                )
                result = integration_controller.run(
                    target=spec_text,
                    initial_prompt=integration_prompt,
                    draft_override=integrated_source,
                    draft_source_override="architect_integrator",
                )
                session = result.payload
    else:
        prompt = _initial_prompt(plan_packet, queue)
        result = controller.run(target=spec_text, initial_prompt=prompt)
        session = result.payload
    final_attempt = session.get("attempts", [{}])[-1] if session.get("attempts") else {}
    final_source = final_attempt.get("draft", "")
    spec_issues = _validate_structured_spec_output(final_source, plan)
    import_graph = analyze_import_graph(
        _structured_spec_file_map(final_source, plan),
        external_roots=set(getattr(plan, "allowed_libraries", [])),
    )
    smoke_result = _run_integration_smoke_test(final_source, plan)
    final_gate_issues = [*spec_issues, *smoke_result["issues"]]
    if session.get("final_status") == "completed" and final_gate_issues:
        session["final_status"] = "manual_review_required"
        session["human_review"] = {
            "status": "manual_review_required",
            "reason": (
                "integration_smoke_failed"
                if smoke_result["issues"]
                else "structured_spec_validation_failed"
            ),
            "blocking_findings": [],
            "blocking_violations": final_gate_issues,
            "behavior_issues": [],
            "formal_issues": [],
            "last_retry_prompt": final_attempt.get("retry_prompt", ""),
            "diagnostic_deltas": final_attempt.get("diagnostic_deltas", []),
            "repair_directives": final_attempt.get("repair_directives", []),
            "suggested_human_decision": (
                "Regenerate with the required components from the structured spec or escalate the spec packet to an architect."
            ),
        }

    artifact_path = ""
    if paths is not None:
        artifact_path = str(paths.run_dir)
        manager.save_session(
            session,
            paths,
            metadata={
                "spec_path": str(spec_path),
                "model": model,
                "plan": {
                    "task_type": plan.task_type,
                    "language": plan.language,
                    "app_name": plan.app_name,
                    "game_kind": plan.game_kind,
                    "route_hint": plan.route_hint,
                    "allowed_libraries": plan.allowed_libraries,
                    "state_rules": plan.state_machine_constraints,
                    "dependency_graph": plan.dependency_graph_context,
                },
                "contract_queue": {
                    **contract_metadata,
                    "deal_scaffold": queue.to_deal_scaffold() if queue.contracts else "",
                    "worker_packets": queue.to_worker_packets(),
                    "sequential_execution": [asdict(result) for result in contract_execution_results],
                },
                "structured_spec_validation": {
                    "is_compliant": not spec_issues,
                    "issues": spec_issues,
                    "import_graph": asdict(import_graph),
                    "integration_smoke": smoke_result,
                },
                "model_telemetry": [
                    *contract_metadata.get("architect_contract_telemetry", []),
                    *architect_model_supplier.telemetry,
                ],
            },
        )
        manager.checkpoint(
            {
                "version": 1,
                "kind": "structured_spec",
                "phase": "terminal",
                "spec_path": str(spec_path),
                "model": model,
                "accepted_sources": [
                    result.source
                    for result in contract_execution_results
                    if result.status == "accepted" and result.source
                ],
                "contract_results": [
                    asdict(result) for result in contract_execution_results
                ],
                "session": session,
            },
            paths,
        )

    repair_workers = [
        attempt.get("repair_worker", "")
        for attempt in session.get("attempts", [])
        if attempt.get("repair_worker")
    ]
    formal = final_attempt.get("formal_validation", {})
    validation = final_attempt.get("validation", {})
    print(
        json.dumps(
            {
                "status": session.get("final_status"),
                "attempts": len(session.get("attempts", [])),
                "repair_workers": repair_workers,
                "architect_contracts_requested": contract_metadata["architect_contracts_requested"],
                "architect_api_configured": contract_metadata["architect_api_configured"],
                "architect_contracts_parsed": contract_metadata["architect_contracts_parsed"],
                "architect_contract_plan_parsed": contract_metadata["architect_contract_plan_parsed"],
                "architect_contract_plan_applied": contract_metadata["architect_contract_plan_applied"],
                "architect_contract_count": contract_metadata["architect_contract_count"],
                "architect_contract_error": contract_metadata["architect_contract_error"],
                "contract_queue_mode": "sequential" if queue.contracts else "bulk",
                "contract_queue_results": [
                    {
                        "name": result.name,
                        "status": result.status,
                        "issues": result.issues,
                    }
                    for result in contract_execution_results
                ],
                "final_static_compliant": validation.get("is_compliant", True),
                "final_static_violations": validation.get("violations", []),
                "structured_spec_compliant": not spec_issues,
                "structured_spec_issues": spec_issues,
                "integration_smoke_status": smoke_result["status"],
                "integration_smoke_compliant": smoke_result["is_compliant"],
                "integration_smoke_issues": smoke_result["issues"],
                "final_formal_tool": formal.get("tool", ""),
                "final_formal_compliant": formal.get("is_compliant", True),
                "final_formal_issues": formal.get("issues", []),
                "artifact_path": artifact_path,
            },
            indent=2,
        )
    )
    return 0 if session.get("final_status") == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a structured external spec through the generic harness.")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--save-artifacts", action="store_true")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--architect-after-repair-attempts", type=int, default=None)
    parser.add_argument("--no-architect-contracts", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="Stop after architect queue planning and print the planned contracts.")
    parser.add_argument(
        "--resume-run",
        help="Resume a structured-spec contract queue from ARTIFACT_ROOT/<run_id>/checkpoint.json.",
    )
    args = parser.parse_args()
    return run_spec(
        spec_path=args.spec,
        model=args.model,
        artifact_root=args.artifact_root,
        save_artifacts=args.save_artifacts,
        max_retries=args.max_retries,
        architect_after_repair_attempts=args.architect_after_repair_attempts,
        use_architect_contracts=not args.no_architect_contracts,
        config_path=args.config,
        plan_only=args.plan_only,
        resume_run_id=args.resume_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
