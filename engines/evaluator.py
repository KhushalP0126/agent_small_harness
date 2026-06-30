from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from engines.base import BaseEngine, EngineFinding
from engines.branching_engine import BranchingEngine
from engines.decomposition_engine import DecompositionEngine
from engines.hazards_engine import HazardsEngine
from engines.math_engine import MathEngine

SEVERITY_WEIGHTS = {
    "Low": 1,
    "Medium": 3,
    "High": 8,
}
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES_PATH = ROOT / "data" / "engine_cases.json"


@dataclass
class EngineCase:
    name: str
    path: Path
    expected_by_engine: dict[str, list[str]]


@dataclass
class EngineScore:
    engine: str
    cases_run: int
    cases_matched: int
    total_findings: int
    weighted_severity: int
    recall: float


@dataclass
class EngineEvaluation:
    engine_scores: list[EngineScore]
    case_results: list[dict]
    overall_recall: float
    overall_weighted_severity: int


def default_engines() -> list[BaseEngine]:
    return [MathEngine(), HazardsEngine(), BranchingEngine()]


def load_cases(cases_path: Path | None = None) -> list[EngineCase]:
    cases_path = cases_path or DEFAULT_CASES_PATH
    raw = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = []
    for case in raw["cases"]:
        cases.append(
            EngineCase(
                name=case["name"],
                path=ROOT / case["path"],
                expected_by_engine=case["expected_by_engine"],
            )
        )
    return cases


def _matched_expected(findings: list[EngineFinding], expected_summaries: list[str]) -> bool:
    summaries = {finding.summary for finding in findings}
    return all(expected in summaries for expected in expected_summaries)


def evaluate_engines(
    engines: list[BaseEngine] | None = None,
    cases: list[EngineCase] | None = None,
) -> EngineEvaluation:
    engines = engines or default_engines()
    cases = cases or load_cases()

    per_engine_matches = {engine.name: 0 for engine in engines}
    per_engine_runs = {engine.name: 0 for engine in engines}
    per_engine_findings = {engine.name: 0 for engine in engines}
    per_engine_weighted = {engine.name: 0 for engine in engines}
    case_results: list[dict] = []

    for case in cases:
        source = case.path.read_text(encoding="utf-8")
        case_result = {"case": case.name, "path": str(case.path), "engines": []}
        for engine in engines:
            findings = engine.scan(source)
            expected = case.expected_by_engine.get(engine.name, [])
            matched = _matched_expected(findings, expected) if expected else True
            if expected:
                per_engine_runs[engine.name] += 1
                per_engine_matches[engine.name] += int(matched)

            per_engine_findings[engine.name] += len(findings)
            per_engine_weighted[engine.name] += sum(
                SEVERITY_WEIGHTS.get(finding.severity, 0) for finding in findings
            )
            case_result["engines"].append(
                {
                    "engine": engine.name,
                    "matched_expectation": matched,
                    "expected_summaries": expected,
                    "findings": [asdict(finding) for finding in findings],
                }
            )
        case_results.append(case_result)

    engine_scores = []
    for engine in engines:
        cases_run = per_engine_runs[engine.name]
        cases_matched = per_engine_matches[engine.name]
        recall = 1.0 if cases_run == 0 else cases_matched / cases_run
        engine_scores.append(
            EngineScore(
                engine=engine.name,
                cases_run=cases_run,
                cases_matched=cases_matched,
                total_findings=per_engine_findings[engine.name],
                weighted_severity=per_engine_weighted[engine.name],
                recall=recall,
            )
        )

    total_cases = sum(score.cases_run for score in engine_scores)
    total_matches = sum(score.cases_matched for score in engine_scores)
    overall_recall = 1.0 if total_cases == 0 else total_matches / total_cases
    overall_weighted_severity = sum(score.weighted_severity for score in engine_scores)

    return EngineEvaluation(
        engine_scores=engine_scores,
        case_results=case_results,
        overall_recall=overall_recall,
        overall_weighted_severity=overall_weighted_severity,
    )


def evaluate_project_source(source_path: Path) -> dict:
    source = source_path.read_text(encoding="utf-8")
    findings = []
    for engine in default_engines():
        findings.extend(asdict(finding) for finding in engine.scan(source))
    return {
        "source_path": str(source_path),
        "decomposition": asdict(DecompositionEngine().decompose(source)),
        "findings": findings,
    }
