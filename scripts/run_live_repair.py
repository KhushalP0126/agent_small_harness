from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.behavior_spec import BehaviorSpecAgent
from agents.coder import CoderAgent
from agents.generation_controller import GenerationController
from agents.historian import HistorianAgent
from agents.repair_strategy import RepairStrategyAgent
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaModelSupplier
from benchmarker import HISTORY_PATH, ROOT as REPO_ROOT
from validation.behavior import mixed_hard_case_spec


DEFAULT_FIXTURE = REPO_ROOT / "data" / "snippets" / "mixed_hard_case.py"


def run_live_repair(
    fixture_path: Path = DEFAULT_FIXTURE,
    model: str = DEFAULT_OLLAMA_MODEL,
    max_retries: int = 3,
    template: str = "auto",
    debug: bool = True,
    record_history: bool = False,
    gen_id: str = "live-repair",
) -> dict:
    source = fixture_path.read_text(encoding="utf-8")
    behavior_spec = BehaviorSpecAgent().for_source(source) or mixed_hard_case_spec()
    strategy = RepairStrategyAgent()
    forced_template = "" if template == "auto" else template
    template_name, template_code = strategy.select_initial_template(source, forced_template=forced_template)
    initial_prompt = CoderAgent().build_repair_prompt(
        source,
        behavior_spec=behavior_spec,
        template_name=template_name,
        template_code=template_code,
    )
    supplier = OllamaModelSupplier(model=model)
    controller = GenerationController(
        max_retries=max_retries,
        draft_supplier=lambda _prompt: template_code or source,
        repair_supplier=supplier.repair_draft,
        policy={"max_cyclomatic_complexity": 4},
        behavior_spec=behavior_spec,
        repair_strategy=strategy,
        debug=debug,
    )
    result = controller.run(
        target=str(fixture_path.relative_to(REPO_ROOT) if fixture_path.is_relative_to(REPO_ROOT) else fixture_path),
        initial_prompt=initial_prompt,
    )
    session = result.payload
    if record_history:
        HistorianAgent(HISTORY_PATH).record_repair_outcome(
            gen_id=gen_id,
            session=session,
            template_name=template_name,
            prompt_label="live-repair",
        )
    return session


def print_session_summary(session: dict) -> None:
    print("\n--- Live Repair Session ---")
    print(f"Target: {session['target']}")
    print(f"Route: {session['route']}")
    print(f"Final status: {session['final_status']}")
    for attempt in session["attempts"]:
        validation = attempt["validation"]
        behavior_validation = attempt["behavior_validation"]
        violations = validation["violations"]
        behavior_issues = behavior_validation["issues"]
        print(f"\n[Attempt {attempt['attempt']}]")
        print(f"  static compliant: {validation['is_compliant']}")
        print(f"  behavior compliant: {behavior_validation['is_compliant']}")
        print(f"  static violations: {len(violations)}")
        print(f"  behavior issues: {len(behavior_issues)}")
        print(f"  changed: {attempt['changed']}")
        print(f"  diff chars: {len(attempt['diff'])}")
        for violation in violations:
            print(
                "  - "
                f"{violation['kind']}: {violation['current_value']} "
                f"allowed {violation['allowed_value']}"
            )
        for issue in behavior_issues:
            print(
                "  - "
                f"behavior {issue['case']}: expected {issue['expected']} "
                f"got {issue['actual']}"
            )
            if issue["details"]:
                print(f"    details: {issue['details']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the live Ollama repair loop against one fixture.")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--template", default="auto", help="Use `auto`, `scoring_matrix`, or an empty string.")
    parser.add_argument("--quiet", action="store_true", help="Disable controller debug prints.")
    parser.add_argument("--json", action="store_true", help="Print the full session JSON.")
    parser.add_argument(
        "--record-history",
        action="store_true",
        help="Record the repair outcome (and any learned template lesson) into history.json.",
    )
    parser.add_argument("--gen-id", default="live-repair", help="gen_id to tag the recorded repair outcome.")
    args = parser.parse_args()

    session = run_live_repair(
        fixture_path=Path(args.fixture).resolve(),
        model=args.model,
        max_retries=args.max_retries,
        template=args.template,
        debug=not args.quiet,
        record_history=args.record_history,
        gen_id=args.gen_id,
    )
    if args.json:
        print(json.dumps(session, indent=2))
    else:
        print_session_summary(session)


if __name__ == "__main__":
    main()
