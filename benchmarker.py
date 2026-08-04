from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaModelSupplier
from agents.coder import CoderAgent
from agents.dependency import DependencyAgent
from agents.generation_controller import GenerationController
from agents.historian import HistorianAgent
from agents.postprocessor import PostprocessorAgent
from agents.preprocessor import PreprocessorAgent
from agents.scope_tracker import ScopeTrackerAgent
from engines.branching_engine import BranchingEngine
from engines.decomposition_engine import DecompositionEngine
from engines.evaluator import evaluate_engines
from engines.hazards_engine import HazardsEngine
from engines.math_engine import MathEngine
from prompt.constraint_types import BranchConstraint, LoopConstraint, MutationConstraint
from validation.policy import serialize_validation_result, validate_findings

ROOT = Path(__file__).resolve().parent
HISTORY_PATH = ROOT / "history.json"
CONVENTIONS_PATH = ROOT / "docs" / "reference" / "conventions.md"


@dataclass
class BenchmarkSample:
    size: int
    seconds: float


@dataclass
class BenchmarkReport:
    function_name: str
    samples: list[BenchmarkSample]
    normalized_costs: list[float]
    mean_normalized_cost: float
    relative_spread: float
    classification: str


def linear_scan(values: Sequence[int]) -> int:
    total = 0
    for value in values:
        total += value
    return total


def time_call(func: Callable[[Sequence[int]], int], values: Sequence[int], repeats: int) -> float:
    durations = []
    for _ in range(repeats):
        start = time.perf_counter()
        func(values)
        durations.append(time.perf_counter() - start)
    return min(durations)


def verify_linear_growth(
    func: Callable[[Sequence[int]], int],
    input_sizes: Iterable[int],
    repeats: int = 7,
    tolerance: float = 0.35,
) -> BenchmarkReport:
    samples: list[BenchmarkSample] = []
    normalized_costs: list[float] = []

    for size in input_sizes:
        data = list(range(size))
        seconds = time_call(func, data, repeats=repeats)
        samples.append(BenchmarkSample(size=size, seconds=seconds))
        normalized_costs.append(seconds / size)

    mean_normalized = statistics.mean(normalized_costs)
    spread = max(abs(cost - mean_normalized) for cost in normalized_costs)
    relative_spread = 0.0 if mean_normalized == 0 else spread / mean_normalized
    classification = "linear" if relative_spread <= tolerance else "non-linear"

    return BenchmarkReport(
        function_name=func.__name__,
        samples=samples,
        normalized_costs=normalized_costs,
        mean_normalized_cost=mean_normalized,
        relative_spread=relative_spread,
        classification=classification,
    )


def run_day1_pipeline(gen_id: str = "day1-bootstrap") -> dict:
    goal = "Setup engines directory and verify linear growth rate."
    pre = PreprocessorAgent(CONVENTIONS_PATH).run(gen_id=gen_id, goal=goal)
    historian = HistorianAgent(HISTORY_PATH)
    history_result = historian.run(gen_id=gen_id)
    deps = DependencyAgent().run(project_root=ROOT)
    scope = ScopeTrackerAgent().run(shared_state={"ROOT": str(ROOT)})
    source = Path(__file__).read_text(encoding="utf-8")
    ir = DecompositionEngine().decompose(source)
    findings = [
        asdict(finding)
        for engine in (MathEngine(), HazardsEngine(), BranchingEngine())
        for finding in engine.scan(source)
    ]
    raw_findings = [
        finding
        for engine in (MathEngine(), HazardsEngine(), BranchingEngine())
        for finding in engine.scan(source)
    ]
    loop_constraint = LoopConstraint(
        max_depth=max((loop.depth for loop in ir.loops), default=0),
        deepest_path=next((loop.path for loop in ir.loops if loop.depth == max((item.depth for item in ir.loops), default=0)), []),
        mutation_sites=ir.loop_mutation_targets,
    )
    branch_constraint = BranchConstraint(
        cyclomatic_complexity=next(
            finding["metrics"]["cyclomatic_complexity"]
            for finding in findings
            if finding["engine"] == "engine-3-branching"
        ),
        branch_count=next(
            finding["metrics"]["conditional_branch_count"]
            for finding in findings
            if finding["engine"] == "engine-3-branching"
        ),
        risk_level=next(
            finding["metrics"]["risk_level"]
            for finding in findings
            if finding["engine"] == "engine-3-branching"
        ),
        dominant_conditions=[branch.condition for branch in ir.branches[:5]],
    )
    mutation_constraint = MutationConstraint(
        explicit_globals=ir.explicit_globals,
        module_level_mutations=sorted(
            {
                mutation.target
                for mutation in ir.mutations
                if mutation.target in ir.module_state_names and mutation.scope != "module"
            }
        ),
        shared_containers=ir.module_state_names,
    )
    coder = CoderAgent().run(
        gen_id=gen_id,
        goal=goal,
        lessons=history_result.payload["lessons_learned"],
        conventions=[
            "Only write code that satisfies the current gen_id.",
            "Prefer O(N) or O(log N) approaches.",
            "Refactor when high-severity findings appear.",
        ],
        dependency_context=[deps.payload["context_hint"], scope.payload["constraint_rule"]],
        loop_constraint=loop_constraint,
        branch_constraint=branch_constraint,
        mutation_constraint=mutation_constraint,
    )
    report = verify_linear_growth(linear_scan, [2_000, 4_000, 8_000, 16_000])
    engine_evaluation = evaluate_engines()
    validation_result = validate_findings(raw_findings)
    controller_session = GenerationController(
        max_retries=1,
        draft_supplier=lambda _prompt: source,
        repair_supplier=lambda draft, _retry_prompt: draft,
    ).run(target="benchmarker.py", initial_prompt=coder.payload["prompt"])
    post = PostprocessorAgent().run(
        artifacts=["benchmarker.py", "history.json", "engines/", "agents/"]
    )

    generation_record = {
        "gen_id": gen_id,
        "goal": goal,
        "benchmark": {
            "function_name": report.function_name,
            "classification": report.classification,
            "relative_spread": report.relative_spread,
            "samples": [asdict(sample) for sample in report.samples],
        },
        "engine_findings": findings,
        "engine_evaluation": asdict(engine_evaluation),
        "validation": serialize_validation_result(validation_result),
        "coder_prompt": coder.payload["prompt"],
        "controller_session": controller_session.payload,
    }
    historian.append_generation(generation_record)

    return {
        "preprocessor": pre.payload,
        "historian": history_result.payload,
        "coder": coder.payload,
        "dependency": deps.payload,
        "scope": scope.payload,
        "decomposition": asdict(ir),
        "engines": findings,
        "engine_evaluation": asdict(engine_evaluation),
        "validation": serialize_validation_result(validation_result),
        "controller_session": controller_session.payload,
        "benchmark": asdict(report),
        "postprocessor": post.payload,
    }


def build_ollama_controller(
    model: str = DEFAULT_OLLAMA_MODEL,
    max_retries: int = 2,
    debug: bool = False,
) -> GenerationController:
    supplier = OllamaModelSupplier(model=model)
    return GenerationController(
        max_retries=max_retries,
        draft_supplier=supplier.generate_draft,
        repair_supplier=supplier.repair_draft,
        debug=debug,
    )


def main() -> None:
    result = run_day1_pipeline()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
