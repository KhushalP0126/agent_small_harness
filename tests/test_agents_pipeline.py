import json
import tempfile
import unittest
from pathlib import Path

from agents.behavior_spec import DEFAULT_BEHAVIOR_CASES, BehaviorSpecAgent
from agents.engine_registry import EngineRegistry
from agents.generation_controller import GenerationController
from agents.historian import HistorianAgent
from agents.parse_contract import (
    ParseContractAgent,
    ParseFailure,
    ParseSuccess,
    detect_language,
)
from agents.repair_strategy import (
    MANUAL_REVIEW,
    MODEL_ONLY,
    TEMPLATE_DIRECTED,
    RepairStrategyAgent,
)
from benchmarker import ROOT
from engines.branching_engine import BranchingEngine
from engines.hazards_engine import HazardsEngine
from engines.math_engine import MathEngine
from validation.behavior import mixed_hard_case_spec
from validation.policy import validate_findings
from validation.types import Violation


MIXED = ROOT / "data" / "snippets" / "mixed_hard_case.py"
LINEAR = ROOT / "data" / "snippets" / "linear_safe.py"
C_SOURCE = "#include <stdio.h>\nint main(void) { return 0; }\n"


def _python_findings(source: str):
    return [
        finding
        for engine in (MathEngine(), HazardsEngine(), BranchingEngine())
        for finding in engine.scan(source)
    ]


class ParseContractTests(unittest.TestCase):
    def test_detects_python_and_c(self) -> None:
        self.assertEqual(detect_language("def analyze(matrix):\n    return 0\n"), "python")
        self.assertEqual(detect_language(C_SOURCE), "c")
        self.assertEqual(detect_language("", filename="foo.c"), "c")
        self.assertEqual(detect_language("", language="Python"), "python")

    def test_python_success_returns_tree(self) -> None:
        result = ParseContractAgent().parse("def analyze(matrix):\n    return 0\n")
        self.assertIsInstance(result, ParseSuccess)
        self.assertEqual(result.language, "python")
        self.assertIsNotNone(result.tree)

    def test_python_syntax_error_is_parse_failure(self) -> None:
        result = ParseContractAgent().parse("def bad(:\n")
        self.assertIsInstance(result, ParseFailure)
        self.assertEqual(result.finding.engine, "engine-parse-contract")
        self.assertEqual(result.finding.summary, "Draft parse failure")

    def test_unknown_language_is_gated(self) -> None:
        # A language with no registered engine set stays gated (C/C++ are now supported
        # via tree-sitter, so use a genuinely unregistered language here).
        result = ParseContractAgent().parse("fn main() {}", language="rust")
        self.assertIsInstance(result, ParseFailure)
        self.assertEqual(result.language, "rust")
        self.assertEqual(result.finding.summary, "Unsupported language")
        self.assertEqual(result.finding.engine, "engine-parse-contract")


class EngineRegistryTests(unittest.TestCase):
    def test_default_routes_python_engines(self) -> None:
        registry = EngineRegistry.default()
        self.assertTrue(registry.has_language("python"))
        names = [engine.name for engine in registry.engines_for("python")]
        self.assertEqual(names, ["engine-1-math", "engine-2-hazards", "engine-3-branching"])

    def test_findings_match_direct_engine_calls(self) -> None:
        source = MIXED.read_text(encoding="utf-8")
        registry_findings = EngineRegistry.default().findings_for(source, "python")
        registry_summaries = [(finding.engine, finding.summary) for finding in registry_findings]
        direct_summaries = [(finding.engine, finding.summary) for finding in _python_findings(source)]
        self.assertEqual(registry_summaries, direct_summaries)

    def test_unknown_language_returns_no_findings(self) -> None:
        self.assertEqual(EngineRegistry.default().findings_for("anything", "rust"), [])

    def test_register_adds_language(self) -> None:
        registry = EngineRegistry()
        registry.register("python", [MathEngine])
        self.assertEqual([engine.name for engine in registry.engines_for("python")], ["engine-1-math"])


class PythonHazardPolicyTests(unittest.TestCase):
    def test_read_only_module_mapping_is_not_mutation(self) -> None:
        source = "LOOKUP = {'a': 1}\n\ndef analyze(value):\n    return LOOKUP.get(value, 0)\n"
        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}
        self.assertNotIn("Module-level container mutation hazard", summaries)
        self.assertTrue(validate_findings(findings).is_compliant)

    def test_external_dependency_becomes_policy_violation(self) -> None:
        source = "import numpy\n\ndef analyze(values):\n    return values\n"
        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}
        self.assertIn("External dependency usage", summaries)
        violations = validate_findings(findings).violations
        self.assertEqual(violations[0].kind, "external_dependency")
        self.assertEqual(violations[0].repair_hint, "use_standard_library")


class RepairStrategyTests(unittest.TestCase):
    def test_selects_scoring_matrix_template(self) -> None:
        name, code = RepairStrategyAgent().select_initial_template(MIXED.read_text(encoding="utf-8"))
        self.assertEqual(name, "scoring_matrix")
        self.assertIn("def analyze", code)

    def test_parse_error_forces_model_only(self) -> None:
        violation = Violation(
            kind="parse_error",
            engine="engine-parse-contract",
            severity="High",
            summary="Draft parse failure",
            rationale="bad syntax",
            current_value="line 1",
            allowed_value="valid Python syntax",
        )
        decision = RepairStrategyAgent().decide("def bad(:\n", violations=[violation])
        self.assertEqual(decision.mode, MODEL_ONLY)

    def test_template_directed_when_pattern_and_issues(self) -> None:
        source = MIXED.read_text(encoding="utf-8")
        violations = validate_findings(_python_findings(source)).violations
        decision = RepairStrategyAgent().decide(source, violations=violations)
        self.assertEqual(decision.mode, TEMPLATE_DIRECTED)
        self.assertEqual(decision.template_name, "scoring_matrix")
        self.assertIn("def analyze", decision.template_code)

    def test_manual_review_when_no_actionable_path(self) -> None:
        decision = RepairStrategyAgent().decide(
            "def analyze(matrix):\n    return 0\n",
            violations=[],
            behavior_issues=[],
        )
        self.assertEqual(decision.mode, MANUAL_REVIEW)

    def test_repair_instructions_are_violation_based_not_task_specific(self) -> None:
        violations = [
            Violation(
                kind="cyclomatic_complexity",
                engine="engine-3-branching",
                severity="High",
                summary="Cyclomatic complexity 12",
                rationale="too many paths",
                current_value="12",
                allowed_value="<= 7",
                repair_hint="split_function",
            ),
            Violation(
                kind="external_dependency",
                engine="engine-2-hazards",
                severity="High",
                summary="External dependency usage",
                rationale="external import",
                current_value="numpy",
                allowed_value="standard library imports only",
                repair_hint="use_standard_library",
            ),
        ]
        decision = RepairStrategyAgent().decide("import numpy\n", violations=violations)
        instructions = "\n".join(decision.repair_instructions)
        self.assertIn("small single-purpose helper functions", instructions)
        self.assertIn("standard-library", instructions)
        self.assertNotIn("snake", instructions.lower())
        self.assertNotIn("calculate_new_head", instructions)

    def test_repair_strategy_consumes_engine_diagnostic_refactor(self) -> None:
        violation = Violation(
            kind="cyclomatic_complexity",
            engine="engine-3-branching",
            severity="High",
            summary="Cyclomatic complexity 8",
            rationale="too many paths",
            current_value="8",
            allowed_value="<= 7",
            repair_hint="split_function",
            evidence={
                "diagnostic": {
                    "recommended_refactor": "Extract branch-heavy decisions into helper functions.",
                }
            },
        )
        decision = RepairStrategyAgent().decide("def analyze(x):\n    return x\n", violations=[violation])
        self.assertIn("Extract branch-heavy decisions", "\n".join(decision.repair_instructions))


class BehaviorSpecTests(unittest.TestCase):
    def test_for_source_maps_scoring_matrix(self) -> None:
        spec = BehaviorSpecAgent().for_source(MIXED.read_text(encoding="utf-8"))
        self.assertIsNotNone(spec)
        self.assertEqual(spec.function_name, "analyze")

    def test_for_source_returns_none_for_unknown(self) -> None:
        self.assertIsNone(BehaviorSpecAgent().for_source("def analyze(matrix):\n    return 0\n"))

    def test_load_from_file_matches_inline_spec(self) -> None:
        loaded = BehaviorSpecAgent().load_from_file(DEFAULT_BEHAVIOR_CASES, "scoring_matrix")
        inline = mixed_hard_case_spec()
        self.assertEqual(loaded.function_name, inline.function_name)
        self.assertEqual(
            [(case.name, case.args, case.expected) for case in loaded.cases],
            [(case.name, case.args, case.expected) for case in inline.cases],
        )

    def test_loaded_spec_validates_fixture(self) -> None:
        agent = BehaviorSpecAgent()
        loaded = agent.load_from_file()
        result = agent.validate(MIXED.read_text(encoding="utf-8"), loaded)
        self.assertTrue(result.is_compliant)


class HistorianLearningTests(unittest.TestCase):
    def _temp_history(self) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"schema_version": 1, "lessons_learned": [], "generations": []}, handle)
        handle.close()
        return Path(handle.name)

    def test_records_outcome_and_learns_template(self) -> None:
        path = self._temp_history()
        try:
            historian = HistorianAgent(path)
            session = {
                "target": "mixed",
                "route": "iterative_retry",
                "final_status": "completed",
                "attempts": [
                    {
                        "attempt": 0,
                        "validation": {"is_compliant": False, "violations": [{"kind": "cyclomatic_complexity"}]},
                        "behavior_validation": {"is_compliant": False, "issues": [{"case": "mixed rows"}]},
                    },
                    {
                        "attempt": 1,
                        "validation": {"is_compliant": True, "violations": []},
                        "behavior_validation": {"is_compliant": True, "issues": []},
                    },
                ],
            }
            outcome = historian.record_repair_outcome(
                "genX", session, template_name="scoring_matrix", prompt_label="live-repair"
            )
            self.assertTrue(outcome["succeeded"])
            self.assertEqual(len(outcome["summary"]["failed_attempts"]), 1)
            self.assertEqual(historian.successful_templates(), {"scoring_matrix": 1})
            lessons = historian.run("genX").payload["lessons_learned"]
            self.assertTrue(any("scoring_matrix" in lesson for lesson in lessons))
        finally:
            path.unlink()

    def test_record_increments_existing_lesson(self) -> None:
        path = self._temp_history()
        try:
            historian = HistorianAgent(path)
            session = {"final_status": "completed", "attempts": []}
            historian.record_repair_outcome("genX", session, template_name="scoring_matrix")
            historian.record_repair_outcome("genY", session, template_name="scoring_matrix")
            self.assertEqual(historian.successful_templates(), {"scoring_matrix": 2})
            history = json.loads(path.read_text(encoding="utf-8"))
            entries = [
                entry
                for entry in history["lessons_learned"]
                if entry["id"] == "repair-template-scoring_matrix"
            ]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["success_count"], 2)
        finally:
            path.unlink()

    def test_failed_session_is_not_learned(self) -> None:
        path = self._temp_history()
        try:
            historian = HistorianAgent(path)
            session = {"final_status": "manual_review_required", "attempts": []}
            historian.record_repair_outcome("genX", session, template_name="scoring_matrix")
            self.assertEqual(historian.successful_templates(), {})
        finally:
            path.unlink()


class ControllerIntegrationTests(unittest.TestCase):
    def test_unsupported_language_routes_to_manual_review(self) -> None:
        controller = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: "fn main() {}",
            language="rust",
        )
        result = controller.run(target="rust-draft", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "manual_review_required")
        violations = result.payload["attempts"][0]["validation"]["violations"]
        self.assertEqual(violations[0]["kind"], "parse_error")

    def test_completes_with_injected_default_registry(self) -> None:
        source = LINEAR.read_text(encoding="utf-8")
        controller = GenerationController(max_retries=1, draft_supplier=lambda _prompt: source)
        result = controller.run(target="linear", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "completed")

    def test_strategy_instructions_are_injected_into_retry_prompt(self) -> None:
        violating_source = "import numpy\n\ndef analyze(values):\n    return values\n"
        repaired_source = "def analyze(values):\n    return values\n"

        def repair_supplier(_draft: str, retry_prompt: str) -> str:
            self.assertIn("TARGETED REPAIR INSTRUCTIONS:", retry_prompt)
            self.assertIn("standard-library", retry_prompt)
            self.assertIn("Remove third-party imports", retry_prompt)
            self.assertNotIn("snake", retry_prompt.lower())
            return repaired_source

        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: violating_source,
            repair_supplier=repair_supplier,
            repair_strategy=RepairStrategyAgent(),
        )
        result = controller.run(target="dependency-repair", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "completed")

    def test_each_valid_attempt_runs_full_python_engine_set(self) -> None:
        violating_source = MIXED.read_text(encoding="utf-8")
        repaired_source = LINEAR.read_text(encoding="utf-8")
        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: violating_source,
            repair_supplier=lambda _draft, _retry_prompt: repaired_source,
        )
        result = controller.run(target="engine-coverage", initial_prompt="generate")
        expected_engines = {"engine-1-math", "engine-2-hazards", "engine-3-branching"}
        self.assertEqual(result.payload["final_status"], "completed")
        self.assertEqual(len(result.payload["attempts"]), 2)
        for attempt in result.payload["attempts"]:
            self.assertEqual({finding["engine"] for finding in attempt["findings"]}, expected_engines)


if __name__ == "__main__":
    unittest.main()
