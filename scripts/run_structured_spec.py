from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.artifact_manager import ArtifactManager
from agents.config_loader import DEFAULT_CONFIG_PATH, load_config
from agents.generation_controller import GenerationController
from agents.plan_mode import PlanModeAgent
from agents.repair_strategy import RepairStrategyAgent
from backends.architect_client import (
    ArchitectConfig,
    ArchitectModelSupplier,
    ContractArchitectError,
    ContractArchitectSupplier,
)
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaModelSupplier
from kernel.function_contracts import ContractQueue, DealExample, FunctionContract


DEFAULT_ARTIFACT_ROOT = Path("artifacts/runs")
FALLBACK_SNAKE_CONTRACTS = [
    "opposite_direction",
    "next_head",
    "hits_wall",
    "hits_self",
    "choose_food",
    "step_state",
    "create_initial_state",
    "handle_input",
    "render",
    "main",
]


def _contract_queue_from_architect(plan_packet: str, plan_context: str, profile) -> tuple[ContractQueue, dict]:
    config = ArchitectConfig(contract_profile=profile)
    metadata = {
        "architect_contracts_requested": True,
        "architect_api_configured": config.api_key_configured,
        "architect_contracts_parsed": False,
        "architect_contract_count": 0,
        "architect_contract_error_code": "",
        "architect_contract_error": "",
        "architect_contracts_fallback_used": False,
    }
    if not config.api_key_configured:
        metadata["architect_contract_error_code"] = "architect_contract_missing_api_key"
        metadata["architect_contract_error"] = "architect API key not configured"
        return ContractQueue(), metadata

    try:
        queue = ContractArchitectSupplier(profile=profile).build_contract_queue(
            plan_packet=plan_packet,
            preserved_context=plan_context,
        )
    except ContractArchitectError as exc:
        metadata["architect_contract_error_code"] = exc.code
        metadata["architect_contract_error"] = str(exc)
        return ContractQueue(), metadata
    except Exception as exc:  # noqa: BLE001 - surfaced in run metadata for review
        metadata["architect_contract_error_code"] = "architect_contract_unexpected_failure"
        metadata["architect_contract_error"] = f"{type(exc).__name__}: {exc}"
        return ContractQueue(), metadata

    metadata["architect_contracts_parsed"] = True
    metadata["architect_contract_count"] = len(queue.contracts)
    return queue, metadata


def _fallback_contract_queue(plan) -> tuple[ContractQueue, bool]:
    if plan.app_name != "snake":
        return ContractQueue(), False
    contracts = [
        FunctionContract(
            name="opposite_direction",
            signature="def opposite_direction(a: tuple[int, int], b: tuple[int, int]) -> bool",
            purpose="Return True when two movement vectors are direct opposites.",
            examples=[
                DealExample("opposite_direction((1, 0), (-1, 0))", "True"),
                DealExample("opposite_direction((1, 0), (0, 1))", "False"),
            ],
        ),
        FunctionContract(
            name="next_head",
            signature="def next_head(head: tuple[int, int], direction: tuple[int, int]) -> tuple[int, int]",
            purpose="Return the next grid coordinate after moving one cell.",
            examples=[DealExample("next_head((5, 5), (1, 0))", "(6, 5)")],
        ),
        FunctionContract(
            name="hits_wall",
            signature="def hits_wall(head: tuple[int, int], width: int, height: int) -> bool",
            purpose="Return True when the head coordinate is outside the board.",
            examples=[
                DealExample("hits_wall((-1, 5), 20, 20)", "True"),
                DealExample("hits_wall((10, 10), 20, 20)", "False"),
            ],
        ),
        FunctionContract(
            name="hits_self",
            signature="def hits_self(head: tuple[int, int], body: list[tuple[int, int]]) -> bool",
            purpose="Return True when the head coordinate overlaps the snake body.",
            examples=[DealExample("hits_self((3, 3), [(1, 1), (3, 3)])", "True")],
        ),
    ]
    for name in FALLBACK_SNAKE_CONTRACTS:
        if name not in {contract.name for contract in contracts}:
            contracts.append(
                FunctionContract(
                    name=name,
                    signature=f"def {name}(*args, **kwargs)",
                    purpose=f"Implement the `{name}` component required by the structured spec.",
                )
            )
    return ContractQueue(contracts), True


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
    return issues


def run_spec(
    spec_path: Path,
    model: str,
    artifact_root: Path,
    save_artifacts: bool,
    max_retries: int,
    architect_after_repair_attempts: int | None,
    use_architect_contracts: bool,
    config_path: Path,
) -> int:
    config = load_config(config_path)
    spec_text = spec_path.read_text(encoding="utf-8")
    plan_mode = PlanModeAgent()
    plan = plan_mode.plan(spec_text)
    plan_packet = plan_mode.to_worker_packet(plan)
    plan_context = plan_mode.to_prompt_context(plan)
    queue = ContractQueue()
    contract_metadata = {
        "architect_contracts_requested": False,
        "architect_api_configured": ArchitectConfig().api_key_configured,
        "architect_contracts_parsed": False,
        "architect_contract_count": 0,
        "architect_contract_error_code": "",
        "architect_contract_error": "",
        "architect_contracts_fallback_used": False,
    }
    if use_architect_contracts:
        queue, contract_metadata = _contract_queue_from_architect(
            plan_packet,
            plan_context,
            config.execution.architect.contract,
        )
        if not queue.contracts:
            fallback_queue, fallback_used = _fallback_contract_queue(plan)
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

    supplier = OllamaModelSupplier(model=model)
    architect_supplier = (
        ArchitectModelSupplier(config=ArchitectConfig(repair_profile=config.execution.architect.repair)).repair_draft
        if architect_after_repair_attempts is not None
        else None
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
    )
    prompt = _initial_prompt(plan_packet, queue)
    result = controller.run(target=spec_text, initial_prompt=prompt)
    session = result.payload
    final_attempt = session.get("attempts", [{}])[-1] if session.get("attempts") else {}
    final_source = final_attempt.get("draft", "")
    spec_issues = _validate_structured_spec_output(final_source, plan)
    if session.get("final_status") == "completed" and spec_issues:
        session["final_status"] = "manual_review_required"
        session["human_review"] = {
            "status": "manual_review_required",
            "reason": "structured_spec_validation_failed",
            "blocking_findings": [],
            "blocking_violations": spec_issues,
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
    if save_artifacts:
        manager = ArtifactManager(artifact_root)
        paths = manager.create_run(prefix=f"structured_spec_{spec_path.stem}")
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
                },
                "structured_spec_validation": {
                    "is_compliant": not spec_issues,
                    "issues": spec_issues,
                },
            },
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
                "architect_contract_count": contract_metadata["architect_contract_count"],
                "architect_contract_error": contract_metadata["architect_contract_error"],
                "final_static_compliant": validation.get("is_compliant", True),
                "final_static_violations": validation.get("violations", []),
                "structured_spec_compliant": not spec_issues,
                "structured_spec_issues": spec_issues,
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
    )


if __name__ == "__main__":
    raise SystemExit(main())
