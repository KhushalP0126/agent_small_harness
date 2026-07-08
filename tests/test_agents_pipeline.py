import json
import tempfile
import unittest
from pathlib import Path

from agents.behavior_spec import DEFAULT_BEHAVIOR_CASES, BehaviorSpecAgent
from agents.engine_registry import EngineRegistry
from agents.generation_controller import GenerationController
from agents.historian import HistorianAgent
from agents.job_store import JsonlJobStore
from agents.library_discovery import LibraryDiscoveryAgent
from agents.parse_contract import (
    ParseContractAgent,
    ParseFailure,
    ParseSuccess,
    detect_language,
)
from agents.plan_mode import PlanModeAgent
from agents.routing_policy import RoutingPolicyAgent
from agents.task_classifier import TaskClassifierAgent
from agents.repair_strategy import (
    MANUAL_REVIEW,
    MODEL_ONLY,
    TEMPLATE_DIRECTED,
    RepairStrategyAgent,
)
from benchmarker import ROOT
from engines.bounds_engine import BoundsEngine
from engines.branching_engine import BranchingEngine
from engines.cost_engine import CostEngine
from engines.hazards_engine import HazardsEngine
from engines.lint_engine import LintEngine
from engines.library_registry import LibraryRegistry
from engines.math_engine import MathEngine
from engines.state_flow_engine import StateFlowEngine
from validation.behavior import BehaviorCase, FunctionBehaviorSpec, mixed_hard_case_spec
from validation.policy import validate_findings
from validation.types import Violation
from scripts.approve_library import merge_proposal


MIXED = ROOT / "data" / "snippets" / "mixed_hard_case.py"
LINEAR = ROOT / "data" / "snippets" / "linear_safe.py"
C_SOURCE = "#include <stdio.h>\nint main(void) { return 0; }\n"


def _python_findings(source: str):
    return [
        finding
        for engine in (
            MathEngine(),
            HazardsEngine(),
            BranchingEngine(),
            CostEngine(),
            BoundsEngine(),
            StateFlowEngine(),
            LintEngine(),
        )
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
        self.assertEqual(
            names,
            [
                "engine-1-math",
                "engine-2-hazards",
                "engine-3-branching",
                "engine-4-cost",
                "engine-6-bounds",
                "engine-7-state-flow",
                "engine-5-lint",
            ],
        )

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


class LibraryRegistryTests(unittest.TestCase):
    def test_loads_registered_library_schema(self) -> None:
        registry = LibraryRegistry()
        pygame = registry.get("pygame")
        self.assertIsNotNone(pygame)
        self.assertIn("draw.rect", pygame.allowed_calls)
        self.assertTrue(registry.is_registered("pygame"))


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

    def test_registered_library_allowed_call_is_not_external_or_unknown(self) -> None:
        source = (
            "import pygame\n\n"
            "def draw_box(screen, color, rect):\n"
            "    pygame.draw.rect(screen, color, rect)\n"
        )
        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}
        self.assertNotIn("External dependency usage", summaries)
        self.assertNotIn("Unknown registered-library API usage", summaries)
        self.assertTrue(validate_findings(findings).is_compliant)

    def test_registered_library_alias_unknown_call_becomes_policy_violation(self) -> None:
        source = (
            "import pygame as pg\n\n"
            "def draw_box(screen, color, rect):\n"
            "    pg.rect(screen, color, rect)\n"
        )
        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}
        self.assertIn("Unknown registered-library API usage", summaries)
        violations = validate_findings(findings).violations
        self.assertEqual(violations[0].kind, "unknown_api")
        self.assertEqual(violations[0].repair_hint, "use_registered_api")
        self.assertIn("pygame.rect", violations[0].current_value)
        self.assertIn("pygame.draw.rect", violations[0].evidence["diagnostic"]["recommended_refactor"])

    def test_registered_library_from_import_allowed_call_is_valid(self) -> None:
        source = (
            "from pygame import draw\n\n"
            "def draw_box(screen, color, rect):\n"
            "    draw.rect(screen, color, rect)\n"
        )
        findings = HazardsEngine().scan(source)
        summaries = {finding.summary for finding in findings}
        self.assertNotIn("Unknown registered-library API usage", summaries)
        self.assertTrue(validate_findings(findings).is_compliant)


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

    def test_unknown_api_instruction_is_general_and_uses_diagnostic_context(self) -> None:
        violation = Violation(
            kind="unknown_api",
            engine="engine-2-hazards",
            severity="High",
            summary="Unknown registered-library API usage",
            rationale="unknown library call",
            current_value="pygame.rect",
            allowed_value="registered library schema",
            repair_hint="use_registered_api",
            evidence={
                "diagnostic": {
                    "recommended_refactor": "Use pygame.draw.rect(...) instead of pygame.rect(...).",
                }
            },
        )
        decision = RepairStrategyAgent().decide("import pygame\n", violations=[violation])
        instructions = "\n".join(decision.repair_instructions)
        self.assertIn("pygame.draw.rect", instructions)
        self.assertIn("registered library schema", instructions)
        self.assertNotIn("snake", instructions.lower())


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


class ScalabilityAgentTests(unittest.TestCase):
    def test_plan_mode_extracts_harness_ready_function_spec(self) -> None:
        prompt = (
            "Hey, can you please write a Python function named parse_key_value_lines. "
            "It should parse user input efficiently. "
            "parse_key_value_lines('a=1') == {'a': 1}"
        )
        result = PlanModeAgent().run(prompt).payload
        self.assertEqual(result["language"], "python")
        self.assertEqual(result["task_type"], "general_code")
        self.assertEqual(result["target_function"], "parse_key_value_lines")
        self.assertEqual(result["behavior_cases"][0]["call"], "parse_key_value_lines('a=1')")
        self.assertEqual(result["behavior_cases"][0]["expected"], "{'a': 1}")
        self.assertFalse(result["needs_user_clarification"])
        self.assertIn("avoid repeated linear membership", "\n".join(result["performance_constraints"]))
        self.assertIn("handle malformed input explicitly", "\n".join(result["security_constraints"]))
        self.assertIn("PLAN MODE SPEC:", result["prompt_context"])
        self.assertIn("@deal.example(lambda: parse_key_value_lines('a=1') == {'a': 1})", result["deal_contracts"])
        self.assertIn("Deal contract candidates", result["prompt_context"])

    def test_plan_mode_requests_clarification_for_unspecified_function_task(self) -> None:
        result = PlanModeAgent().run("Build a helper function that returns the right answer.").payload
        self.assertTrue(result["needs_user_clarification"])
        self.assertIn("What exact function name", "\n".join(result["questions"]))
        self.assertIn("What input/output examples", "\n".join(result["questions"]))

    def test_plan_mode_preserves_library_context_without_auto_approval(self) -> None:
        result = PlanModeAgent().run("Build a pandas data cleaner function clean_rows(df).").payload
        self.assertEqual(result["task_type"], "data")
        self.assertEqual(result["allowed_libraries"], ["pandas"])
        self.assertTrue(any("opaque dependency" in item for item in result["adapter_contracts"]))
        self.assertEqual(result["route_hint"], "template_or_small_worker")
        self.assertIn("pandas", result["prompt_context"])
        self.assertIn("ADAPTER RULES:", result["worker_packet"])

    def test_plan_mode_extracts_state_machine_constraints_for_section_parser(self) -> None:
        prompt = (
            "Write a Python function named parse_sectioned_config(text). "
            "It receives lines of configuration text. A section header is [section]. "
            "Key value records inside a section use key=value and require exactly one equals sign. "
            "Ignore records before any section or with empty keys. Later valid records overwrite earlier values."
        )
        result = PlanModeAgent().run(prompt).payload
        constraints = "\n".join(result["state_machine_constraints"])
        self.assertIn("active section variable initialized to None", constraints)
        self.assertIn("exactly one equals sign", constraints)
        self.assertIn("ignore key/value records until an active section exists", constraints)
        self.assertIn("State-machine constraints", result["prompt_context"])

    def test_task_classifier_extracts_language_libraries_and_route_hint(self) -> None:
        classification = TaskClassifierAgent().classify("Build a pygame game in Python")
        self.assertEqual(classification.language, "python")
        self.assertEqual(classification.task_type, "game")
        self.assertEqual(classification.libraries, ["pygame"])
        self.assertEqual(classification.route_hint, "template_or_small_worker")

    def test_routing_policy_escalates_human_review_payload_to_architect(self) -> None:
        route = RoutingPolicyAgent().decide(
            {"route_hint": "small_worker"},
            human_review={"reason": "stagnant_repair"},
        )
        self.assertEqual(route.worker, "architect_llm")
        self.assertEqual(route.max_retries, 1)

    def test_routing_policy_uses_historian_stats_when_successful(self) -> None:
        stats = {
            "groups": {
                "library:pandas": {
                    "success_rate": 0.9,
                    "best_observed_route": "library_context_first",
                }
            }
        }
        route = RoutingPolicyAgent().decide(
            {"task_type": "data", "language": "python", "libraries": ["pandas"], "route_hint": "small_worker"},
            stats=stats,
        )
        self.assertEqual(route.worker, "library_context_first")
        self.assertEqual(route.reason, "selected from historian route statistics")

    def test_routing_policy_escalates_state_machine_tasks_after_one_worker_attempt(self) -> None:
        route = RoutingPolicyAgent().decide(
            {
                "task_type": "general_code",
                "language": "python",
                "route_hint": "small_worker",
                "state_machine_constraints": ["track parser state explicitly"],
            }
        )
        self.assertEqual(route.worker, "architect_after_one_small_attempt")
        self.assertEqual(route.max_retries, 1)

    def test_job_store_records_status_and_events(self) -> None:
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        path = Path(handle.name)
        handle.close()
        try:
            store = JsonlJobStore(path)
            job = store.create_job("task-tracker")
            store.append_event(job.job_id, "classification", {"task_type": "general_code"})
            store.update_status(job.job_id, "completed")
            loaded = store.get_job(job.job_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.status, "completed")
            self.assertEqual(loaded.events[0]["event_type"], "classification")
        finally:
            path.unlink()

    def test_library_discovery_reads_public_stdlib_symbols_without_importing(self) -> None:
        discovered = LibraryDiscoveryAgent().discover("json")
        self.assertTrue(discovered.available)
        self.assertIn("dump", discovered.public_symbols)
        self.assertIn("dump", discovered.proposal["allowed_calls"])

    def test_library_discovery_writes_reviewable_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = LibraryDiscoveryAgent().write_proposal("json", Path(tmpdir))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["library"], "json")
            self.assertTrue(payload["available"])
            self.assertEqual(payload["proposal"]["proposal_status"], "candidate")
            self.assertIn("loads", payload["proposal"]["allowed_calls"])

    def test_approve_library_merges_proposal_into_temp_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proposal_path = LibraryDiscoveryAgent().write_proposal("json", root / "proposals")
            registry_path = root / "library_registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "libraries": {
                            "json": {
                                "allowed_calls": ["dump"],
                                "context": "Existing json context.",
                                "unknown_api_repair": "Use known json APIs.",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = merge_proposal(registry_path, proposal_path)
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(result["old_version"], "1.0")
            self.assertEqual(result["new_version"], "1.1")
            self.assertIn("loads", registry["libraries"]["json"]["allowed_calls"])
            self.assertEqual(registry["libraries"]["json"]["context"], "Existing json context.")


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

    def test_build_run_record_extracts_engine_and_task_labels(self) -> None:
        path = self._temp_history()
        try:
            historian = HistorianAgent(path)
            session = {
                "target": "task",
                "route": "iterative_retry",
                "final_status": "manual_review_required",
                "human_review": {"reason": "stagnant_repair"},
                "attempts": [
                    {
                        "validation": {
                            "violations": [
                                {"engine": "engine-4-cost", "kind": "algorithmic_cost"},
                                {"engine": "engine-2-hazards", "kind": "unknown_api"},
                            ]
                        },
                        "behavior_validation": {"issues": [{"case": "empty"}]},
                    }
                ],
            }
            record = historian.build_run_record(
                session,
                classification={"task_type": "data", "language": "python", "libraries": ["pandas"]},
                route_used="template_then_small_llm",
                model="qwen",
            )
            self.assertEqual(record["task_type"], "data")
            self.assertEqual(record["libraries"], ["pandas"])
            self.assertEqual(record["failed_engines"], ["engine-2-hazards", "engine-4-cost"])
            self.assertEqual(record["failed_kinds"], ["algorithmic_cost", "unknown_api"])
            self.assertEqual(record["human_review_reason"], "stagnant_repair")
        finally:
            path.unlink()

    def test_run_stats_aggregate_jsonl_for_router_lookup(self) -> None:
        history_path = self._temp_history()
        runs_handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        stats_handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        runs_path = Path(runs_handle.name)
        stats_path = Path(stats_handle.name)
        runs_handle.close()
        stats_handle.close()
        try:
            historian = HistorianAgent(history_path)
            historian.append_run_sample(
                runs_path,
                {
                    "task_type": "data",
                    "language": "python",
                    "libraries": ["pandas"],
                    "route_used": "library_context_first",
                    "repair_attempts": 2,
                    "final_status": "completed",
                    "failed_engines": ["engine-2-hazards"],
                    "failed_kinds": ["unknown_api"],
                    "contribution": {"label": "small_helped_architect", "score": 0.5},
                },
            )
            historian.append_run_sample(
                runs_path,
                {
                    "task_type": "data",
                    "language": "python",
                    "libraries": ["pandas"],
                    "route_used": "small_llm",
                    "repair_attempts": 3,
                    "final_status": "manual_review_required",
                    "failed_engines": ["engine-2-hazards"],
                    "failed_kinds": ["unknown_api"],
                    "contribution": {"label": "small_no_progress", "score": 0.0},
                },
            )
            stats = historian.aggregate_run_stats(runs_path, stats_path)
            pandas_stats = stats["groups"]["library:pandas"]
            self.assertEqual(pandas_stats["total_runs"], 2)
            self.assertEqual(pandas_stats["success_rate"], 0.5)
            self.assertEqual(pandas_stats["avg_contribution_score"], 0.25)
            self.assertEqual(pandas_stats["top_contribution"], "small_helped_architect")
            self.assertEqual(pandas_stats["top_failed_engine"], "engine-2-hazards")
            self.assertTrue(stats_path.read_text(encoding="utf-8").strip())
        finally:
            history_path.unlink()
            runs_path.unlink()
            stats_path.unlink()


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
            self.assertIn("CRITICAL BUG FIX REQUIRED", retry_prompt)
            self.assertIn("Remove third-party imports", retry_prompt)
            self.assertIn("standard-library", retry_prompt)
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
        expected_engines = {
            "engine-1-math",
            "engine-2-hazards",
            "engine-3-branching",
            "engine-4-cost",
            "engine-6-bounds",
            "engine-7-state-flow",
            "engine-5-lint",
        }
        self.assertEqual(result.payload["final_status"], "completed")
        self.assertEqual(len(result.payload["attempts"]), 2)
        for attempt in result.payload["attempts"]:
            self.assertEqual({finding["engine"] for finding in attempt["findings"]}, expected_engines)

    def test_repeated_violation_records_no_improvement_delta(self) -> None:
        bad_v1 = """
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
        bad_v2 = bad_v1.replace("return 7", "return value")
        repaired = LINEAR.read_text(encoding="utf-8")

        def repair_supplier(_draft: str, retry_prompt: str) -> str:
            if "PREVIOUS REPAIR SIGNAL:" in retry_prompt:
                self.assertIn("no improvement", retry_prompt)
                return repaired
            return bad_v2

        controller = GenerationController(
            max_retries=2,
            draft_supplier=lambda _prompt: bad_v1,
            repair_supplier=repair_supplier,
        )
        result = controller.run(target="delta-tracking", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "completed")
        self.assertEqual(result.payload["attempts"][1]["diagnostic_deltas"][0]["kind"], "cyclomatic_complexity")
        self.assertEqual(result.payload["attempts"][1]["diagnostic_deltas"][0]["delta"], 0)
        self.assertFalse(result.payload["attempts"][1]["diagnostic_deltas"][0]["improved"])

    def test_architect_supplier_takes_over_after_small_worker_threshold(self) -> None:
        bad_v1 = """
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
        bad_v2 = bad_v1.replace("return 7", "return value")
        repaired = LINEAR.read_text(encoding="utf-8")
        small_calls = []
        architect_calls = []

        def small_supplier(_draft: str, retry_prompt: str) -> str:
            small_calls.append(retry_prompt)
            return bad_v2

        def architect_supplier(_draft: str, retry_prompt: str) -> str:
            architect_calls.append(retry_prompt)
            self.assertIn("DIAGNOSTIC DELTAS:", retry_prompt)
            return repaired

        controller = GenerationController(
            max_retries=2,
            draft_supplier=lambda _prompt: bad_v1,
            repair_supplier=small_supplier,
            architect_supplier=architect_supplier,
            architect_after_repair_attempts=1,
        )
        result = controller.run(target="architect-escalation", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "completed")
        self.assertEqual(len(small_calls), 1)
        self.assertEqual(len(architect_calls), 1)
        self.assertEqual(result.payload["attempts"][0]["repair_worker"], "small_worker")
        self.assertEqual(result.payload["attempts"][1]["repair_worker"], "architect_llm")

    def test_architect_supplier_handles_small_worker_stagnation_after_threshold(self) -> None:
        bad_source = """
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
        repaired = LINEAR.read_text(encoding="utf-8")
        small_calls = []
        architect_calls = []

        def small_supplier(draft: str, retry_prompt: str) -> str:
            small_calls.append(retry_prompt)
            return draft

        def architect_supplier(_draft: str, retry_prompt: str) -> str:
            architect_calls.append(retry_prompt)
            return repaired

        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: bad_source,
            repair_supplier=small_supplier,
            architect_supplier=architect_supplier,
            architect_after_repair_attempts=1,
        )
        result = controller.run(target="architect-stagnation", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "completed")
        self.assertEqual(len(small_calls), 1)
        self.assertEqual(len(architect_calls), 1)
        self.assertEqual(result.payload["attempts"][0]["repair_worker"], "small_worker->architect_llm")

    def test_architect_supplier_error_routes_to_manual_review(self) -> None:
        bad_source = """
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

        def small_supplier(draft: str, _retry_prompt: str) -> str:
            return draft

        def architect_supplier(_draft: str, _retry_prompt: str) -> str:
            raise RuntimeError("empty architect response")

        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: bad_source,
            repair_supplier=small_supplier,
            architect_supplier=architect_supplier,
            architect_after_repair_attempts=1,
        )
        result = controller.run(target="architect-error", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "manual_review_required")
        self.assertEqual(result.payload["human_review"]["reason"], "repair_supplier_error")
        self.assertIn("empty architect response", result.payload["attempts"][0]["repair_error"])

    def test_architect_static_engine_failure_stops_without_second_architect_retry(self) -> None:
        bad_v1 = """
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
        bad_v2 = bad_v1.replace("return 7", "return value")
        architect_bad = bad_v1.replace("return 7", "return value + 1")
        architect_calls = []

        def small_supplier(_draft: str, _retry_prompt: str) -> str:
            return bad_v2

        def architect_supplier(_draft: str, retry_prompt: str) -> str:
            architect_calls.append(retry_prompt)
            return architect_bad

        controller = GenerationController(
            max_retries=3,
            draft_supplier=lambda _prompt: bad_v1,
            repair_supplier=small_supplier,
            architect_supplier=architect_supplier,
            architect_after_repair_attempts=1,
        )
        result = controller.run(target="architect-static-stop", initial_prompt="generate")

        self.assertEqual(result.payload["final_status"], "manual_review_required")
        self.assertEqual(result.payload["human_review"]["reason"], "metric_scope_ambiguous")
        self.assertEqual(len(architect_calls), 1)
        self.assertEqual(result.payload["attempts"][-1]["draft_source_worker"], "architect_llm")
        self.assertFalse(result.payload["attempts"][-1]["validation"]["is_compliant"])

    def test_architect_behavior_clean_complexity_only_routes_to_metric_scope_review(self) -> None:
        initial_source = """
def parse_sectioned_config(text):
    return {}
"""
        architect_source = """
def parse_sectioned_config(text):
    result = {}
    active_section = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('[') and line.endswith(']'):
            name = line[1:-1].strip()
            if name:
                active_section = name
                result.setdefault(active_section, {})
            continue
        if active_section is None:
            continue
        if line.count('=') != 1:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if key:
            result[active_section][key] = value
    return result
"""
        spec = FunctionBehaviorSpec(
            function_name="parse_sectioned_config",
            cases=[
                BehaviorCase(name="empty", args=("",), expected={}),
                BehaviorCase(
                    name="malformed",
                    args=("orphan=skip\n[]\n[ok]\n=bad\nx=1=2\nx = one\nx = two",),
                    expected={"ok": {"x": "two"}},
                ),
            ],
        )
        architect_calls = []

        def architect_supplier(_draft: str, retry_prompt: str) -> str:
            architect_calls.append(retry_prompt)
            return architect_source

        controller = GenerationController(
            max_retries=3,
            draft_supplier=lambda _prompt: initial_source,
            repair_supplier=lambda draft, _prompt: draft,
            architect_supplier=architect_supplier,
            architect_after_repair_attempts=0,
            behavior_spec=spec,
        )
        result = controller.run(target="metric-scope", initial_prompt="generate")

        self.assertEqual(result.payload["final_status"], "manual_review_required")
        self.assertEqual(result.payload["human_review"]["reason"], "metric_scope_ambiguous")
        self.assertEqual(len(architect_calls), 1)
        self.assertTrue(result.payload["attempts"][-1]["behavior_validation"]["is_compliant"])
        self.assertEqual(
            [violation["kind"] for violation in result.payload["attempts"][-1]["validation"]["violations"]],
            ["cyclomatic_complexity"],
        )

    def test_architect_parse_error_still_routes_to_static_gate_failed(self) -> None:
        bad_source = """
def analyze(value):
    if value:
        return value
    return 0
"""
        architect_calls = []

        def architect_supplier(_draft: str, retry_prompt: str) -> str:
            architect_calls.append(retry_prompt)
            return "def broken(:\n    return 1\n"

        controller = GenerationController(
            max_retries=3,
            draft_supplier=lambda _prompt: bad_source,
            repair_supplier=lambda draft, _prompt: draft,
            architect_supplier=architect_supplier,
            architect_after_repair_attempts=0,
            policy={"max_cyclomatic_complexity": 1},
        )
        result = controller.run(target="architect-parse-error", initial_prompt="generate")

        self.assertEqual(result.payload["final_status"], "manual_review_required")
        self.assertEqual(result.payload["human_review"]["reason"], "architect_static_gate_failed")
        self.assertEqual(len(architect_calls), 1)
        self.assertEqual(result.payload["attempts"][-1]["draft_source_worker"], "architect_llm")
        self.assertEqual(result.payload["attempts"][-1]["validation"]["violations"][0]["kind"], "parse_error")

    def test_unknown_registered_api_feedback_repairs_through_controller(self) -> None:
        violating_source = (
            "import pygame\n\n"
            "def draw_box(screen, color, rect):\n"
            "    pygame.rect(screen, color, rect)\n"
        )
        repaired_source = (
            "import pygame\n\n"
            "def draw_box(screen, color, rect):\n"
            "    pygame.draw.rect(screen, color, rect)\n"
        )

        def repair_supplier(_draft: str, retry_prompt: str) -> str:
            self.assertIn("CRITICAL BUG FIX REQUIRED", retry_prompt)
            self.assertIn("pygame.draw.rect", retry_prompt)
            return repaired_source

        controller = GenerationController(
            max_retries=1,
            draft_supplier=lambda _prompt: violating_source,
            repair_supplier=repair_supplier,
            repair_strategy=RepairStrategyAgent(),
            policy={"allow_lint_errors": True},
        )
        result = controller.run(target="library-api-repair", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "completed")
        self.assertEqual(result.payload["attempts"][0]["validation"]["violations"][0]["kind"], "unknown_api")


if __name__ == "__main__":
    unittest.main()
