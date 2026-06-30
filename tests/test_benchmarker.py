import json
import unittest
from pathlib import Path

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
from agents.repair_templates import detect_scoring_matrix_pattern, get_repair_template, select_repair_template
from engines.decomposition_engine import DecompositionEngine
from engines.evaluator import DEFAULT_CASES_PATH, evaluate_engines, load_cases
from engines.branching_engine import BranchingEngine
from engines.hazards_engine import HazardsEngine
from engines.math_engine import MathEngine
from prompt.builder import build_prompt
from prompt.constraint_types import BranchConstraint, ConstraintBlock, LoopConstraint, MutationConstraint
from prompt.retry_builder import build_retry_prompt
from validation.behavior import mixed_hard_case_spec, validate_function_behavior
from validation.policy import validate_findings


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

    def test_engine_evaluator_matches_fixture_expectations(self) -> None:
        evaluation = evaluate_engines()
        self.assertEqual(evaluation.overall_recall, 1.0)
        score_by_engine = {score.engine: score for score in evaluation.engine_scores}
        self.assertEqual(score_by_engine["engine-1-math"].cases_matched, 5)
        self.assertEqual(score_by_engine["engine-2-hazards"].cases_matched, 5)
        self.assertEqual(score_by_engine["engine-3-branching"].cases_matched, 5)
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
            if "PRIOR FAILED ATTEMPTS:" in retry_prompt:
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
        self.assertIn("PRIOR FAILED ATTEMPTS:", result.payload["attempts"][1]["retry_prompt"])
        self.assertIn("Behavior failure:", result.payload["attempts"][1]["retry_prompt"])

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


if __name__ == "__main__":
    unittest.main()
