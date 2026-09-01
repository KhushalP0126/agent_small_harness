from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.generation_controller import GenerationController
from agents.historian import HistorianAgent
from agents.repair_strategy import RepairStrategyAgent
from agents.task_classifier import TaskClassifierAgent


DEFAULT_CASES = ROOT / "tests/adversarial/prompts.json"
DEFAULT_RUNS = ROOT / "data/runs.jsonl"
DEFAULT_HISTORY = ROOT / "history.json"


def _load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _failure_kinds(session: dict) -> set[str]:
    kinds: set[str] = set()
    for attempt in session.get("attempts", []):
        for violation in attempt.get("validation", {}).get("violations", []):
            kind = violation.get("kind")
            if kind:
                kinds.add(kind)
    return kinds


def _failure_engines(session: dict) -> set[str]:
    engines: set[str] = set()
    for attempt in session.get("attempts", []):
        for violation in attempt.get("validation", {}).get("violations", []):
            engine = violation.get("engine")
            if engine:
                engines.add(engine)
    return engines


def run_cases(cases_path: Path, runs_path: Path, history_path: Path, max_retries: int) -> int:
    cases = _load_cases(cases_path)
    historian = HistorianAgent(history_path)
    classifier = TaskClassifierAgent()
    failures: list[str] = []

    for case in cases:
        draft = case["draft"]
        prompt = case["prompt"]
        controller = GenerationController(
            max_retries=max_retries,
            draft_supplier=lambda _prompt, draft=draft: draft,
            repair_supplier=lambda current, _retry_prompt: current,
            repair_strategy=RepairStrategyAgent(),
        )
        result = controller.run(target=prompt, initial_prompt=prompt)
        session = result.payload
        classification = classifier.classify(prompt)
        run_record = historian.build_run_record(
            session,
            classification={
                "task_type": classification.task_type,
                "language": classification.language,
                "libraries": classification.libraries,
            },
            route_used=session.get("route", ""),
            model="deterministic-adversarial-fixture",
            template_name="",
        )
        run_record["case_name"] = case["name"]
        historian.append_run_sample(runs_path, run_record)

        status = session.get("final_status")
        kinds = _failure_kinds(session)
        engines = _failure_engines(session)
        expected_kinds = set(case.get("expected_failure_kinds", []))
        expected_engines = set(case.get("expected_failure_engines", []))

        if status != case.get("expected_status"):
            failures.append(f"{case['name']}: expected status {case.get('expected_status')} but got {status}")
        missing_kinds = expected_kinds - kinds
        if missing_kinds:
            failures.append(f"{case['name']}: missing failure kinds {sorted(missing_kinds)}")
        missing_engines = expected_engines - engines
        if missing_engines:
            failures.append(f"{case['name']}: missing failure engines {sorted(missing_engines)}")

        print(
            f"[adversarial] {case['name']}: status={status} "
            f"kinds={sorted(kinds)} engines={sorted(engines)}"
        )

    if failures:
        print("\nAdversarial harness failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"\nAdversarial run samples appended to {runs_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run adversarial PEV trap prompts through the harness.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()
    return run_cases(args.cases, args.runs, args.history, args.max_retries)


if __name__ == "__main__":
    raise SystemExit(main())
