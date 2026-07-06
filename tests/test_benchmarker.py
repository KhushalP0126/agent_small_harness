import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agents.config_loader import ConfigError, load_config
from agents.artifact_manager import ArtifactManager
from backends.architect_client import ArchitectConfig, ArchitectModelSupplier
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaGenerationConfig, OllamaModelSupplier
from benchmarker import (
    HISTORY_PATH,
    ROOT,
    build_ollama_controller,
    linear_scan,
    run_day1_pipeline,
    verify_linear_growth,
)
from agents.coder import CoderAgent
from agents.generation_controller import GenerationController
from agents.preprocessor import PreprocessorAgent
from agents.prompt_normalizer import PromptNormalizerAgent
from agents.repair_templates import detect_scoring_matrix_pattern, get_repair_template, select_repair_template
from engines.decomposition_engine import DecompositionEngine
from engines.evaluator import DEFAULT_CASES_PATH, evaluate_engines, load_cases
from engines.branching_engine import BranchingEngine
from engines.cost_engine import CostEngine
from engines.hazards_engine import HazardsEngine
from engines.lint_engine import LintEngine
from engines.math_engine import MathEngine
from prompt.builder import build_prompt
from prompt.constraint_types import BranchConstraint, ConstraintBlock, LoopConstraint, MutationConstraint
from prompt.retry_builder import build_retry_prompt, build_small_worker_retry_prompt
from validation.behavior import mixed_hard_case_spec, validate_function_behavior
from validation.finding_aggregator import aggregate_violations
from validation.formal import FormalIssue, FormalResult, validate_with_crosshair
from validation.policy import validate_findings
from validation.types import Violation
from scripts.run_coding_capability import _behavior_spec, _build_prompt, _worker_contribution
from scripts.review_run import render_review
from scripts.run_formal_experiment import GOOD_SOURCE
from scripts.run_plan_mode_ladder import run_plan_ladder
from scripts.run_worker_limit import _apply_decomposition_prompt


class BenchmarkerTests(unittest.TestCase):
    def test_linear_scan_is_classified_linear(self) -> None:
        report = verify_linear_growth(linear_scan, [1_000, 2_000, 4_000, 8_000], repeats=5, tolerance=0.50)
        self.assertEqual(report.classification, "linear")

    def test_day1_pipeline_writes_generation_record(self) -> None:
        original = HISTORY_PATH.read_text(encoding="utf-8")
        try:
            result = run_day1_pipeline(gen_id="test-day1")
            self.assertIn("benchmark", result)
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            self.assertTrue(any(entry["gen_id"] == "test-day1" for entry in history["generations"]))
        finally:
            HISTORY_PATH.write_text(original, encoding="utf-8")

    def test_project_structure_exists(self) -> None:
        self.assertTrue((ROOT / "agents").is_dir())
        self.assertTrue((ROOT / "engines").is_dir())
        self.assertTrue((ROOT / "history.json").is_file())

    def test_artifact_manager_saves_attempt_files(self) -> None:
        session = {
            "target": "write function",
            "route": "one_pass",
            "max_retries": 1,
            "final_status": "manual_review_required",
            "attempts": [
                {
                    "attempt": 0,
                    "draft": "def analyze(value):\n    return missing\n",
                    "validation": {"is_compliant": True, "violations": []},
                    "behavior_validation": {
                        "is_compliant": False,
                        "issues": [{"case": "basic", "actual": "NameError"}],
                    },
                    "retry_prompt": "fix NameError",
                    "findings": [],
                    "repair_directives": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArtifactManager(Path(tmpdir))
            paths = manager.create_run(prefix="unit")
            manager.save_session(session, paths, metadata={"case_name": "unit"})
            self.assertTrue((paths.run_dir / "metadata.json").is_file())
            self.assertTrue((paths.run_dir / "session_summary.json").is_file())
            self.assertEqual(
                (paths.run_dir / "attempt_0.py").read_text(encoding="utf-8"),
                "def analyze(value):\n    return missing\n",
            )
            self.assertIn(
                "NameError",
                (paths.run_dir / "attempt_0_validation.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (paths.run_dir / "attempt_0_retry_prompt.txt").read_text(encoding="utf-8"),
                "fix NameError",
            )

    def test_prompt_normalizer_removes_conversational_fluff(self) -> None:
        prompt = "Hey, can you please actually build a small task tracker, just no globals."
        result = PromptNormalizerAgent().normalize(prompt)
        self.assertIn("Build a small task tracker", result.normalized_prompt)
        self.assertIn("can you", result.removed_fragments)
        self.assertIn("please", result.removed_fragments)
        self.assertIn("actually", result.removed_fragments)
        self.assertNotIn("please", result.normalized_prompt.lower())

    def test_preprocessor_preserves_raw_goal_and_uses_normalized_goal(self) -> None:
        goal = "I want you to actually create a helper with no globals."
        result = PreprocessorAgent(ROOT / "conventions.md").run("gen-test", goal).payload
        self.assertEqual(result["raw_goal"], goal)
        self.assertIn("Create a helper", result["goal"])
        self.assertNotIn("actually", result["goal"].lower())
        self.assertIn("actually", result["normalization"]["removed_fragments"])

    def test_config_loader_reads_config_yaml(self) -> None:
        config = load_config(ROOT / "config.yaml")
        self.assertEqual(config.execution.models.worker_model, "qwen2.5-coder:1.5b")
        self.assertEqual(config.execution.models.architect_model, "deepseek-v4-pro")
        self.assertEqual(config.execution.models.resolve_for_difficulty(1), "qwen2.5-coder:1.5b")
        self.assertEqual(config.execution.models.resolve_for_difficulty(3), "qwen2.5-coder:3b")
        self.assertEqual(config.execution.models.resolve_for_difficulty(6), "qwen2.5-coder:7b")
        self.assertEqual(config.engines.policy.max_loop_depth, 2)
        self.assertEqual(config.engines.policy.max_cyclomatic_complexity, 7)
        self.assertFalse(config.engines.formal.crosshair_enabled)
        self.assertEqual(config.engines.formal.crosshair_timeout_seconds, 3.0)
        self.assertEqual(config.execution.gates.max_retries, 1)
        self.assertFalse(config.engines.policy.allow_explicit_globals)

    def test_config_loader_rejects_unknown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.yaml"
            path.write_text(
                "engines:\n"
                "  policy:\n"
                "    max_cyclomatic_complexity: 7\n"
                "    surprise_mode: true\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "surprise_mode"):
                load_config(path)

    def test_config_loader_rejects_invalid_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.yaml"
            path.write_text(
                "execution:\n"
                "  gates:\n"
                "    max_retries: -1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "max_retries"):
                load_config(path)

    def test_initial_worker_prompt_includes_plan_mode_context(self) -> None:
        task = {
            "prompt": "Write a Python function named parse_int_list(text). Convert signed integer tokens.",
            "function_name": "parse_int_list",
            "cases": [
                {
                    "name": "signed",
                    "args": ["1, -2, +3"],
                    "expected": [1, -2, 3],
                }
            ],
        }
        prompt = _build_prompt(task, _behavior_spec(task))
        self.assertIn("Compact Plan Mode Packet:", prompt)
        self.assertIn("PLAN PACKET:", prompt)
        self.assertIn("Contract examples for the worker:", prompt)
        self.assertIn("parse_int_list('1, -2, +3') == [1, -2, 3]", prompt)

    def test_plan_mode_ladder_completes(self) -> None:
        with redirect_stdout(StringIO()):
            self.assertEqual(run_plan_ladder(ROOT / "tests" / "plan_mode" / "tasks.json"), 0)

    def test_formal_experiment_source_validates_or_skips(self) -> None:
        result = validate_with_crosshair(GOOD_SOURCE, timeout_seconds=0.1)
        self.assertTrue(result.is_compliant)

    def test_artifact_manager_writes_attempt_timeline_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ArtifactManager(Path(tmpdir))
            paths = manager.create_run(prefix="review-test")
            session = {
                "target": "demo",
                "route": "one_pass",
                "max_retries": 0,
                "final_status": "manual_review_required",
                "human_review": {"reason": "max_retries_exhausted"},
                "attempts": [
                    {
                        "attempt": 0,
                        "draft": "def f():\n    return missing\n",
                        "validation": {"is_compliant": False, "violations": []},
                        "behavior_validation": {"is_compliant": True, "issues": []},
                        "formal_validation": {"is_compliant": True, "issues": [], "skipped": True},
                        "retry_prompt": "fix",
                        "repair_worker": "",
                        "changed": True,
                        "diff": "",
                        "findings": [],
                    }
                ],
            }
            manager.save_session(session, paths)
            self.assertTrue((paths.run_dir / "attempt_timeline.json").is_file())
            review = render_review(paths.run_dir)
            self.assertIn("Attempt timeline:", review)
            self.assertIn("Final status: manual_review_required", review)

    def test_engine_evaluator_matches_fixture_expectations(self) -> None:
        evaluation = evaluate_engines()
        self.assertEqual(evaluation.overall_recall, 1.0)
        score_by_engine = {score.engine: score for score in evaluation.engine_scores}
        self.assertEqual(score_by_engine["engine-1-math"].cases_matched, 5)
        self.assertEqual(score_by_engine["engine-2-hazards"].cases_matched, 5)
        self.assertEqual(score_by_engine["engine-3-branching"].cases_matched, 5)
        self.assertEqual(score_by_engine["engine-4-cost"].recall, 1.0)
        self.assertGreater(score_by_engine["engine-2-hazards"].weighted_severity, 0)

    def test_engine_cases_are_loaded_from_data_folder(self) -> None:
        cases = load_cases()
        self.assertEqual(len(cases), 7)
        self.assertTrue(DEFAULT_CASES_PATH.is_file())
        self.assertTrue(all(case.path.is_file() for case in cases))

    def test_math_engine_reports_loop_depth_metric(self) -> None:
        source = (ROOT / "data" / "snippets" / "triple_nested.py").read_text(encoding="utf-8")
        finding = MathEngine().scan(source)[0]
        self.assertEqual(finding.metrics["max_loop_depth"], 3)
        self.assertEqual(finding.metrics["loop_types"], ["for", "for", "for"])

    def test_hazards_engine_detects_module_container_mutation(self) -> None:
        source = (ROOT / "data" / "snippets" / "mixed_hard_case.py").read_text(encoding="utf-8")
        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}
        self.assertIn("Module-level subscript mutation hazard", summaries)

    def test_branching_engine_reports_cyclomatic_complexity(self) -> None:
        source = (ROOT / "data" / "snippets" / "branchy_but_safe.py").read_text(encoding="utf-8")
        finding = BranchingEngine().scan(source)[0]
        self.assertEqual(finding.metrics["cyclomatic_complexity"], 6)
        self.assertEqual(finding.metrics["risk_level"], "medium")

    def test_cost_engine_flags_linear_membership_inside_loop(self) -> None:
        source = """
def common_items(items, selected: list):
    found = []
    for item in items:
        if item in selected:
            found.append(item)
    return found
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "Linear membership test inside loop")
        result = validate_findings([finding])
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.violations[0].kind, "algorithmic_cost")

    def test_cost_engine_allows_precomputed_set_lookup(self) -> None:
        source = """
def common_items(items, selected):
    selected_set = set(selected)
    return [item for item in items if item in selected_set]
"""
        finding = CostEngine().scan(source)[0]
        self.assertEqual(finding.summary, "No repeated linear membership hotspot detected")
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_lint_engine_is_optional_when_pylint_missing(self) -> None:
        finding = LintEngine(executable="").scan("def analyze(value):\n    return value\n")[0]
        self.assertEqual(finding.summary, "Pylint unavailable")
        self.assertTrue(validate_findings([finding]).is_compliant)

    def test_lint_engine_maps_pylint_error_to_policy_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_pylint = Path(tmpdir) / "fake_pylint"
            fake_pylint.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps([{\n"
                "    'type': 'error',\n"
                "    'module': 'tmp',\n"
                "    'obj': 'analyze',\n"
                "    'line': 2,\n"
                "    'column': 11,\n"
                "    'path': 'tmp.py',\n"
                "    'symbol': 'undefined-variable',\n"
                "    'message': \"Undefined variable 'missing_name'\",\n"
                "    'message-id': 'E0602'\n"
                "}]))\n",
                encoding="utf-8",
            )
            fake_pylint.chmod(fake_pylint.stat().st_mode | stat.S_IXUSR)
            finding = LintEngine(executable=str(fake_pylint)).scan(
                "def analyze(value):\n    return missing_name\n"
            )[0]
        self.assertEqual(finding.summary, "Pylint error")
        self.assertEqual(finding.metrics["symbol"], "undefined-variable")
        result = validate_findings([finding])
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.violations[0].kind, "lint_error")
        self.assertEqual(result.violations[0].repair_hint, "fix_lint_error")

    def test_branching_boundary_at_policy_limit_passes(self) -> None:
        source = """
def analyze(value):
    if value == 0:
        return 0
    if value == 1:
        return 1
    if value == 2:
        return 2
    if value == 3:
        return 3
    if value == 4:
        return 4
    if value == 5:
        return 5
    return 6
"""
        finding = BranchingEngine().scan(source)[0]
        result = validate_findings([finding], policy={"max_cyclomatic_complexity": 7})
        self.assertEqual(finding.metrics["cyclomatic_complexity"], 7)
        self.assertTrue(result.is_compliant)

    def test_branching_boundary_above_policy_limit_reports_diagnostic(self) -> None:
        source = """
def analyze(value):
    if value == 0:
        return 0
    if value == 1:
        return 1
    if value == 2:
        return 2
    if value == 3:
        return 3
    if value == 4:
        return 4
    if value == 5:
        return 5
    if value == 6:
        return 6
    return 7
"""
        finding = BranchingEngine().scan(source)[0]
        result = validate_findings([finding], policy={"max_cyclomatic_complexity": 7})
        self.assertEqual(finding.metrics["cyclomatic_complexity"], 8)
        self.assertFalse(result.is_compliant)
        diagnostic = result.violations[0].evidence["diagnostic"]
        self.assertEqual(diagnostic["violation"], "CYCLOMATIC_COMPLEXITY_EXCEEDED")
        self.assertIn("lookup tables", diagnostic["recommended_refactor"])

    def test_decomposition_engine_extracts_shared_ir(self) -> None:
        source = (ROOT / "data" / "snippets" / "mixed_hard_case.py").read_text(encoding="utf-8")
        ir = DecompositionEngine().decompose(source)
        self.assertEqual(max(loop.depth for loop in ir.loops), 2)
        self.assertIn("STATE", ir.explicit_globals)
        self.assertIn("STATE", ir.module_state_names)

    def test_prompt_builder_emits_constraint_text(self) -> None:
        block = ConstraintBlock(
            goal="Implement a bounded helper",
            loops=LoopConstraint(max_depth=2, deepest_path=["for:rows", "for:row"], mutation_sites=["count"]),
            branches=BranchConstraint(
                cyclomatic_complexity=4,
                branch_count=2,
                risk_level="low",
                dominant_conditions=["if row", "if value < 0"],
            ),
            mutations=MutationConstraint(
                explicit_globals=[],
                module_level_mutations=["STATE"],
                shared_containers=["STATE"],
            ),
            conventions=["No new imports"],
            dependency_context=["No external dependencies required"],
            lessons_learned=["Prefer single-pass iteration"],
        )
        prompt = build_prompt(block)
        self.assertIn("Max loop nesting: 2", prompt)
        self.assertIn("Cyclomatic complexity target: <= 4", prompt)
        self.assertIn("Module-level mutation targets: STATE", prompt)

    def test_pipeline_returns_prompt_and_constraints(self) -> None:
        original = HISTORY_PATH.read_text(encoding="utf-8")
        try:
            result = run_day1_pipeline(gen_id="test-prompt")
            self.assertIn("prompt", result["coder"])
            self.assertIn("constraint_block", result["coder"])
            self.assertIn("GOAL:", result["coder"]["prompt"])
            self.assertIn("validation", result)
            self.assertIn("controller_session", result)
        finally:
            HISTORY_PATH.write_text(original, encoding="utf-8")

    def test_coder_repair_prompt_includes_static_and_behavior_requirements(self) -> None:
        prompt = CoderAgent().build_repair_prompt(
            "def analyze(matrix):\n    return 0\n",
            behavior_spec=mixed_hard_case_spec(),
            context_files=[],
        )
        self.assertIn("Eliminate all global variable mutations.", prompt)
        self.assertIn("Reduce cyclomatic complexity to < 5", prompt)
        self.assertIn("strict input/output parity", prompt)
        self.assertIn("Do not over-optimize", prompt)
        self.assertIn("Behavioral Unit Test Specification:", prompt)
        self.assertIn("analyze([[], [-1, 0, 4, 10, 99, 100]]) == 19", prompt)

    def test_coder_repair_prompt_loads_context_files(self) -> None:
        prompt = CoderAgent().build_repair_prompt("def analyze(matrix):\n    return 0\n")
        self.assertIn("Additional Context From context.md:", prompt)
        self.assertIn("Additional Context From design.md:", prompt)
        self.assertIn("Feedback Injection Prompt", prompt)
        self.assertIn("Visual & Architectural Design Constraints", prompt)

    def test_coder_repair_prompt_includes_template_when_provided(self) -> None:
        template = get_repair_template("scoring_matrix")
        prompt = CoderAgent().build_repair_prompt(
            "def analyze(matrix):\n    return 0\n",
            template_name="scoring_matrix",
            template_code=template,
            context_files=[],
        )
        self.assertIn("Template-Directed Synthesis:", prompt)
        self.assertIn("PRE-VALIDATED TEMPLATE:", prompt)
        self.assertIn("def _score_value(value):", prompt)

    def test_scoring_matrix_template_is_static_and_behavior_compliant(self) -> None:
        template = get_repair_template("scoring_matrix")
        findings = [
            finding
            for engine in (MathEngine(), HazardsEngine(), BranchingEngine())
            for finding in engine.scan(template)
        ]
        static_result = validate_findings(findings, policy={"max_cyclomatic_complexity": 4})
        behavior_result = validate_function_behavior(template, mixed_hard_case_spec())
        self.assertTrue(static_result.is_compliant)
        self.assertTrue(behavior_result.is_compliant)

    def test_scoring_matrix_detector_selects_template_for_fixture(self) -> None:
        source = (ROOT / "data" / "snippets" / "mixed_hard_case.py").read_text(encoding="utf-8")
        self.assertTrue(detect_scoring_matrix_pattern(source))
        self.assertEqual(select_repair_template(source), "scoring_matrix")

    def test_validator_emits_violation_objects(self) -> None:
        source = (ROOT / "data" / "snippets" / "mixed_hard_case.py").read_text(encoding="utf-8")
        findings = [
            finding
            for engine in (MathEngine(), HazardsEngine(), BranchingEngine())
            for finding in engine.scan(source)
        ]
        result = validate_findings(findings)
        self.assertFalse(result.is_compliant)
        violation_kinds = {violation.kind for violation in result.violations}
        self.assertIn("global_mutation", violation_kinds)
        self.assertIn("module_state_mutation", violation_kinds)
        self.assertIn("cyclomatic_complexity", violation_kinds)

    def test_retry_builder_includes_violation_details(self) -> None:
        source = (ROOT / "data" / "snippets" / "mixed_hard_case.py").read_text(encoding="utf-8")
        findings = [
            finding
            for engine in (MathEngine(), HazardsEngine(), BranchingEngine())
            for finding in engine.scan(source)
        ]
        result = validate_findings(findings)
        prompt = build_retry_prompt(source, result.violations)
        self.assertIn("VIOLATIONS:", prompt)
        self.assertIn("Repair hint:", prompt)
        self.assertIn("CURRENT DRAFT:", prompt)

    def test_retry_builder_adds_signed_integer_behavior_hint(self) -> None:
        violation = Violation(
            kind="behavior_mismatch",
            engine="behavior-validator",
            severity="High",
            summary="Failed behavioral output spec",
            rationale="Return value did not match the behavior spec.",
            current_value="mixed returned [1, 3]",
            allowed_value="[1, -2, 3, 4]",
            repair_hint="preserve_behavior",
            evidence={
                "case": {
                    "case": "mixed",
                    "actual": "[1, 3]",
                    "expected": "[1, -2, 3, 4]",
                }
            },
        )
        prompt = build_retry_prompt(
            "def parse_int_list(text):\n    return [int(item) for item in text.split(',') if item.isdigit()]\n",
            [violation],
        )
        self.assertIn("SEMANTIC REPAIR HINTS:", prompt)
        self.assertIn("Do not use str.isdigit() alone", prompt)
        self.assertIn("optional leading + or - signs", prompt)

    def test_small_worker_retry_prompt_is_low_noise(self) -> None:
        violation = Violation(
            kind="behavior_mismatch",
            engine="behavior-validator",
            severity="High",
            summary="Failed behavioral output spec",
            rationale="Return value did not match the behavior spec.",
            current_value="mixed returned [1, 3]",
            allowed_value="[1, -2, 3, 4]",
            repair_hint="preserve_behavior",
            evidence={
                "case": {
                    "case": "mixed",
                    "actual": "[1, 3]",
                    "expected": "[1, -2, 3, 4]",
                }
            },
        )
        prompt = build_small_worker_retry_prompt(
            "def parse_int_list(text):\n    return [int(item) for item in text.split(',') if item.isdigit()]\n",
            [violation],
        )
        self.assertIn("CRITICAL BUG FIX REQUIRED", prompt)
        self.assertIn("YOUR CODE:", prompt)
        self.assertIn("FAILED CHECK:", prompt)
        self.assertIn("FIX DIRECTIVE:", prompt)
        self.assertIn("Do not use str.isdigit() alone", prompt)
        self.assertNotIn("COORDINATED REPAIR PLAN", prompt)
        self.assertNotIn("Kind:", prompt)
        self.assertNotIn("Anchor:", prompt)

    def test_worker_limit_decomposition_prompt_includes_skeleton(self) -> None:
        prompt = _apply_decomposition_prompt(
            "Write code.",
            {"name": "parse_int_list"},
            {
                "strategy": "signed-integer-parser",
                "skeleton": "def _is_signed_integer(token):\n    pass",
            },
        )
        self.assertIn("DECOMPOSITION MODE:", prompt)
        self.assertIn("signed-integer-parser", prompt)
        self.assertIn("Replace every pass statement", prompt)
        self.assertIn("def _is_signed_integer", prompt)

    def test_finding_aggregator_groups_engine_failures_by_function(self) -> None:
        source = """
def parse_key_value_lines(text):
    result = {}
    keys = []
    for line in text.splitlines():
        if line in keys:
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key:
            result[key] = value
            keys.append(key)
    return result
"""
        findings = [
            finding
            for engine in (BranchingEngine(), CostEngine())
            for finding in engine.scan(source)
        ]
        result = validate_findings(findings, policy={"max_cyclomatic_complexity": 3})
        directives = aggregate_violations(source, result.violations)
        self.assertEqual(len(directives), 1)
        directive = directives[0]
        self.assertEqual(directive.function_name, "parse_key_value_lines")
        self.assertIn("cyclomatic_complexity", directive.kinds)
        self.assertIn("algorithmic_cost", directive.kinds)
        self.assertIn("Refactor `parse_key_value_lines` structurally", directive.instruction)

    def test_retry_builder_includes_coordinated_repair_plan(self) -> None:
        source = """
def parse_key_value_lines(text):
    result = {}
    keys = []
    for line in text.splitlines():
        if line in keys:
            continue
        if "=" not in line:
            continue
        result[line] = line
    return result
"""
        findings = [
            finding
            for engine in (BranchingEngine(), CostEngine())
            for finding in engine.scan(source)
        ]
        result = validate_findings(findings, policy={"max_cyclomatic_complexity": 3})
        directives = aggregate_violations(source, result.violations)
        prompt = build_retry_prompt(source, result.violations, directives)
        self.assertIn("COORDINATED REPAIR PLAN:", prompt)
        self.assertIn("Function: parse_key_value_lines", prompt)
        self.assertIn("Required change:", prompt)
        self.assertIn("VIOLATIONS:", prompt)

    def test_generation_controller_completes_with_compliant_draft(self) -> None:
        compliant_source = (ROOT / "data" / "snippets" / "linear_safe.py").read_text(encoding="utf-8")
        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: compliant_source,
        )
        result = controller.run(target="linear-safe-function", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "completed")
        self.assertEqual(len(result.payload["attempts"]), 1)

    def test_generation_controller_requests_manual_review_when_unfixed(self) -> None:
        violating_source = (ROOT / "data" / "snippets" / "mixed_hard_case.py").read_text(encoding="utf-8")
        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: violating_source,
            repair_supplier=lambda draft, _retry_prompt: draft,
        )
        result = controller.run(target="mixed-hard-case", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "manual_review_required")
        self.assertEqual(len(result.payload["attempts"]), 1)
        self.assertTrue(result.payload["attempts"][0]["retry_prompt"])
        self.assertTrue(result.payload["attempts"][0]["changed"])
        review = result.payload["human_review"]
        self.assertEqual(review["status"], "human_review_required")
        self.assertEqual(review["reason"], "stagnant_repair")
        self.assertTrue(review["blocking_violations"])
        self.assertTrue(review["last_retry_prompt"])
        self.assertTrue(review["repair_directives"])
        self.assertIn("CRITICAL BUG FIX REQUIRED", review["last_retry_prompt"])
        self.assertNotIn("COORDINATED REPAIR PLAN:", review["last_retry_prompt"])

    def test_worker_contribution_labels_small_initial_success(self) -> None:
        contribution = _worker_contribution(
            {
                "final_status": "completed",
                "attempts": [{"attempt": 0, "repair_worker": "", "changed": True}],
            }
        )
        self.assertEqual(contribution["label"], "small_solved_initial")
        self.assertEqual(contribution["score"], 1.0)
        self.assertFalse(contribution["architect_used"])

    def test_worker_contribution_labels_architect_takeover(self) -> None:
        contribution = _worker_contribution(
            {
                "final_status": "completed",
                "attempts": [
                    {"attempt": 0, "repair_worker": "small_worker->architect_llm", "changed": True},
                    {"attempt": 1, "repair_worker": "", "changed": True},
                ],
            }
        )
        self.assertEqual(contribution["label"], "architect_solved_after_small_stall")
        self.assertEqual(contribution["score"], 0.0)
        self.assertTrue(contribution["architect_used"])

    def test_generation_controller_stops_on_stagnation(self) -> None:
        violating_source = (ROOT / "data" / "snippets" / "mixed_hard_case.py").read_text(encoding="utf-8")
        controller = GenerationController(
            max_retries=2,
            draft_supplier=lambda _prompt: violating_source,
            repair_supplier=lambda draft, _retry_prompt: draft,
        )
        result = controller.run(target="mixed-hard-case", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "manual_review_required")
        self.assertEqual(len(result.payload["attempts"]), 1)
        self.assertEqual(result.payload["human_review"]["reason"], "stagnant_repair")
        self.assertIn("unchanged repair", result.payload["human_review"]["suggested_human_decision"])

    def test_generation_controller_records_diff_when_changed(self) -> None:
        violating_source = (ROOT / "data" / "snippets" / "mixed_hard_case.py").read_text(encoding="utf-8")
        repaired_source = (ROOT / "data" / "snippets" / "linear_safe.py").read_text(encoding="utf-8")
        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: violating_source,
            repair_supplier=lambda draft, _retry_prompt: repaired_source,
        )
        result = controller.run(target="mixed-hard-case", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "completed")
        self.assertEqual(len(result.payload["attempts"]), 2)
        self.assertTrue(result.payload["attempts"][1]["changed"])
        self.assertTrue(result.payload["attempts"][1]["diff"])

    def test_generation_controller_scopes_small_worker_to_one_finding(self) -> None:
        source = """
def filter_items(items, selected: list):
    result = []
    for item in items:
        if item in selected:
            if item:
                result.append(item)
        else:
            result.append(None)
    return result
"""
        captured_prompts = []

        def repair_supplier(draft: str, retry_prompt: str) -> str:
            captured_prompts.append(retry_prompt)
            return draft

        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: source,
            repair_supplier=repair_supplier,
            policy={"max_cyclomatic_complexity": 3},
        )
        controller.run(target="scope small worker", initial_prompt="generate")
        self.assertEqual(len(captured_prompts), 1)
        self.assertIn("CRITICAL BUG FIX REQUIRED", captured_prompts[0])
        self.assertIn("FIX DIRECTIVE:", captured_prompts[0])
        self.assertIn("precomputing a set or dictionary", captured_prompts[0])
        self.assertNotIn("COORDINATED REPAIR PLAN", captured_prompts[0])
        self.assertNotIn("Kind:", captured_prompts[0])
        self.assertNotIn("cyclomatic_complexity", captured_prompts[0])

    def test_generation_controller_gives_architect_full_finding_set(self) -> None:
        source = """
def filter_items(items, selected: list):
    result = []
    for item in items:
        if item in selected:
            if item:
                result.append(item)
        else:
            result.append(None)
    return result
"""
        captured_prompts = []

        def architect_supplier(draft: str, retry_prompt: str) -> str:
            captured_prompts.append(retry_prompt)
            return draft

        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: source,
            repair_supplier=lambda draft, _prompt: draft,
            architect_supplier=architect_supplier,
            architect_after_repair_attempts=0,
            policy={"max_cyclomatic_complexity": 3},
        )
        controller.run(target="scope architect", initial_prompt="generate")
        self.assertEqual(len(captured_prompts), 1)
        self.assertIn("ARCHITECT MODE:", captured_prompts[0])
        self.assertIn("Kind: cyclomatic_complexity", captured_prompts[0])
        self.assertIn("Kind: algorithmic_cost", captured_prompts[0])

    def test_generation_controller_retries_static_clean_behavior_failure(self) -> None:
        hallucinated_source = "def analyze(matrix):\n    return 0\n"
        repaired_source = """
def _score(value):
    return (
        (value < 0) * 1
        + (value == 0) * 2
        + (0 < value < 10) * 3
        + (10 <= value < 100) * 4
        + (value >= 100) * 5
    )


def analyze(matrix):
    total = 0
    for row in matrix:
        for value in row:
            total += _score(value)
    return total
"""
        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: hallucinated_source,
            repair_supplier=lambda _draft, _retry_prompt: repaired_source,
            behavior_spec=mixed_hard_case_spec(),
        )
        result = controller.run(target="behavior-gate", initial_prompt=CoderAgent().build_repair_prompt(hallucinated_source))
        self.assertEqual(result.payload["final_status"], "completed")
        self.assertTrue(result.payload["attempts"][0]["validation"]["is_compliant"])
        self.assertFalse(result.payload["attempts"][0]["behavior_validation"]["is_compliant"])
        self.assertIn("Failed behavioral output spec", result.payload["attempts"][0]["retry_prompt"])
        self.assertTrue(result.payload["attempts"][1]["behavior_validation"]["is_compliant"])

    def test_generation_controller_injects_prior_failure_feedback(self) -> None:
        broken_source = "def analyze(matrix):\n    return 0\n"

        def repair_supplier(_draft: str, retry_prompt: str) -> str:
            if "PREVIOUS FAILURE:" in retry_prompt:
                return """
def analyze(matrix):
    total = 0
    for row in matrix:
        for value in row:
            total += (
                (value < 0) * 1
                + (value == 0) * 2
                + (0 < value < 10) * 3
                + (10 <= value < 100) * 4
                + (value >= 100) * 5
            )
    return total
"""
            return broken_source.replace("return 0", "return 1")

        controller = GenerationController(
            max_retries=2,
            draft_supplier=lambda _prompt: broken_source,
            repair_supplier=repair_supplier,
            behavior_spec=mixed_hard_case_spec(),
        )
        result = controller.run(target="feedback-loop", initial_prompt="preserve behavior")
        self.assertEqual(result.payload["final_status"], "completed")
        self.assertIn("PREVIOUS FAILURE:", result.payload["attempts"][1]["retry_prompt"])
        self.assertIn("Do not repeat the same output", result.payload["attempts"][1]["retry_prompt"])

    def test_ollama_supplier_uses_small_quantized_default_model(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.calls = []

            def generate(self, **kwargs: object) -> str:
                self.calls.append(kwargs)
                return "def repaired():\n    return 1\n"

        stub_client = StubClient()
        supplier = OllamaModelSupplier(
            client=stub_client,
            config=OllamaGenerationConfig(num_predict=128, num_ctx=2048),
        )
        response = supplier.generate_draft("write a helper")
        self.assertIn("return 1", response)
        self.assertEqual(stub_client.calls[0]["model"], DEFAULT_OLLAMA_MODEL)
        self.assertEqual(stub_client.calls[0]["config"].num_predict, 128)

    def test_ollama_supplier_extracts_fenced_python_code(self) -> None:
        class StubClient:
            def generate(self, **kwargs: object) -> str:
                return "```python\ndef repaired():\n    return 1\n```"

        supplier = OllamaModelSupplier(client=StubClient())
        self.assertEqual(supplier.generate_draft("write code"), "def repaired():\n    return 1")

    def test_ollama_supplier_extracts_unclosed_cpp_fence(self) -> None:
        class StubClient:
            def generate(self, **kwargs: object) -> str:
                return "```cpp\nint main() {\n    return 0;\n}\n"

        supplier = OllamaModelSupplier(client=StubClient())
        self.assertEqual(supplier.generate_draft("write code"), "int main() {\n    return 0;\n}")

    def test_architect_supplier_requires_api_key_placeholder(self) -> None:
        supplier = ArchitectModelSupplier(
            config=ArchitectConfig(
                api_key_env="MISSING_ARCHITECT_TEST_KEY",
                fallback_api_key_env="MISSING_DEEPSEEK_TEST_KEY",
            )
        )
        with self.assertRaisesRegex(RuntimeError, "MISSING_ARCHITECT_TEST_KEY"):
            supplier.repair_draft("def bad():\n    pass\n", "fix it")

    def test_architect_config_defaults_to_deepseek_key_and_model(self) -> None:
        previous_key = os.environ.get("DEEPSEEK_TEST_KEY")
        try:
            os.environ["DEEPSEEK_TEST_KEY"] = "test-secret"
            config = ArchitectConfig(
                api_key_env="MISSING_ARCHITECT_TEST_KEY",
                fallback_api_key_env="DEEPSEEK_TEST_KEY",
                model_env="MISSING_ARCHITECT_MODEL_TEST",
            )
            self.assertTrue(config.api_key_configured)
            self.assertEqual(config.api_key, "test-secret")
            self.assertEqual(config.model, "deepseek-v4-pro")
            self.assertEqual(config.base_url, "https://api.deepseek.com/chat/completions")
        finally:
            if previous_key is None:
                os.environ.pop("DEEPSEEK_TEST_KEY", None)
            else:
                os.environ["DEEPSEEK_TEST_KEY"] = previous_key

    def test_architect_config_reads_dotenv_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "DEEPSEEK_TEST_KEY=dotenv-secret\n"
                "ARCHITECT_TEST_MODEL=deepseek-v4-flash\n",
                encoding="utf-8",
            )
            config = ArchitectConfig(
                api_key_env="MISSING_ARCHITECT_TEST_KEY",
                fallback_api_key_env="DEEPSEEK_TEST_KEY",
                model_env="ARCHITECT_TEST_MODEL",
                env_file=str(env_file),
            )
            self.assertTrue(config.api_key_configured)
            self.assertEqual(config.api_key, "dotenv-secret")
            self.assertEqual(config.model, "deepseek-v4-flash")

    def test_architect_supplier_extracts_code_from_api_response(self) -> None:
        class StubArchitectClient:
            def __init__(self) -> None:
                self.calls = []

            def generate(self, **kwargs: object) -> str:
                self.calls.append(kwargs)
                return "```python\ndef repaired():\n    return 42\n```"

        stub_client = StubArchitectClient()
        supplier = ArchitectModelSupplier(client=stub_client)
        result = supplier.repair_draft("def broken():\n    return missing\n", "fix undefined name")
        self.assertEqual(result, "def repaired():\n    return 42")
        self.assertIn("small worker failed", stub_client.calls[0]["prompt"].lower())
        self.assertIn("fix undefined name", stub_client.calls[0]["prompt"])

    def test_architect_supplier_builds_nagini_formalization_prompt(self) -> None:
        class StubArchitectClient:
            def __init__(self) -> None:
                self.calls = []

            def generate(self, **kwargs: object) -> str:
                self.calls.append(kwargs)
                return "```python\ndef verified(x: int) -> int:\n    return x\n```"

        stub_client = StubArchitectClient()
        supplier = ArchitectModelSupplier(client=stub_client)
        result = supplier.formalize_for_nagini(
            "def identity(x):\n    return x\n",
            spec_context="identity(3) == 3",
            nagini_feedback="missing postcondition",
        )
        self.assertEqual(result, "def verified(x: int) -> int:\n    return x")
        prompt = stub_client.calls[0]["prompt"]
        self.assertIn("ARCHITECT_FORMALIZATION", prompt)
        self.assertIn("Nagini-verifiable Python", prompt)
        self.assertIn("missing postcondition", prompt)

    def test_crosshair_validator_skips_when_dependency_missing_or_passes(self) -> None:
        result = validate_with_crosshair("def identity(x: int) -> int:\n    return x\n", timeout_seconds=0.1)
        self.assertTrue(result.is_compliant)

    def test_generation_controller_blocks_formal_counterexample(self) -> None:
        source = "def identity(x: int) -> int:\n    return x\n"
        formal_result = FormalResult(
            is_compliant=False,
            issues=[
                FormalIssue(
                    tool="crosshair",
                    summary="Counterexample found",
                    details="x=0 violates the postcondition",
                )
            ],
        )
        with patch("agents.generation_controller.validate_with_crosshair", return_value=formal_result):
            controller = GenerationController(
                max_retries=0,
                draft_supplier=lambda _prompt: source,
                crosshair_enabled=True,
            )
            result = controller.run(target="formal-check", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "manual_review_required")
        formal_validation = result.payload["attempts"][0]["formal_validation"]
        self.assertFalse(formal_validation["is_compliant"])
        self.assertEqual(result.payload["human_review"]["formal_issues"][0]["summary"], "Counterexample found")

    def test_build_ollama_controller_exposes_backend_hooks(self) -> None:
        controller = build_ollama_controller(max_retries=1)
        self.assertEqual(controller.max_retries, 1)
        self.assertTrue(callable(controller.draft_supplier))
        self.assertTrue(callable(controller.repair_supplier))

    def test_generation_controller_reports_parse_failure_as_violation(self) -> None:
        controller = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: "```python\ndef bad(:\n```",
        )
        result = controller.run(target="bad-draft", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "manual_review_required")
        violations = result.payload["attempts"][0]["validation"]["violations"]
        self.assertEqual(violations[0]["kind"], "parse_error")
        self.assertEqual(result.payload["human_review"]["reason"], "max_retries_exhausted")
        self.assertEqual(result.payload["human_review"]["blocking_violations"][0]["kind"], "parse_error")


if __name__ == "__main__":
    unittest.main()
