import unittest

from agents.engine_registry import EngineRegistry
from agents.generation_controller import GenerationController
from agents.parse_contract import ParseContractAgent, ParseFailure, ParseSuccess, detect_language
from agents.template_loader import TemplateLibrary
from benchmarker import ROOT
from engines import treesitter_support
from validation.policy import validate_findings


C_DIR = ROOT / "data" / "snippets" / "c"
CPP_DIR = ROOT / "data" / "snippets" / "cpp"
TREE_SITTER = treesitter_support.is_available()
SKIP_REASON = "tree-sitter (or its C/C++ grammars) is not installed"


@unittest.skipUnless(TREE_SITTER, SKIP_REASON)
class TreeSitterParseTests(unittest.TestCase):
    def test_parses_valid_c_and_cpp(self) -> None:
        agent = ParseContractAgent()
        self.assertIsInstance(agent.parse((C_DIR / "simple.c").read_text(), language="c"), ParseSuccess)
        self.assertIsInstance(agent.parse((CPP_DIR / "simple.cpp").read_text(), language="cpp"), ParseSuccess)

    def test_malformed_c_is_parse_failure(self) -> None:
        result = ParseContractAgent().parse("int main(){ for(;;", language="c")
        self.assertIsInstance(result, ParseFailure)
        self.assertEqual(result.finding.engine, "engine-parse-contract")
        self.assertEqual(result.finding.summary, "Draft parse failure")

    def test_detects_cpp(self) -> None:
        self.assertEqual(detect_language("", filename="game.cpp"), "cpp")
        self.assertEqual(
            detect_language("#include <vector>\nint f(){ std::vector<int> v; return 0; }"),
            "cpp",
        )


@unittest.skipUnless(TREE_SITTER, SKIP_REASON)
class TreeSitterEngineTests(unittest.TestCase):
    def test_c_nested_metrics(self) -> None:
        from engines.treesitter_engine import TreeSitterBranchingEngine, TreeSitterMathEngine

        source = (C_DIR / "nested_branchy.c").read_text()
        self.assertEqual(TreeSitterMathEngine("c").scan(source)[0].metrics["max_loop_depth"], 2)
        branching = TreeSitterBranchingEngine("c").scan(source)[0].metrics
        self.assertEqual(branching["cyclomatic_complexity"], 5)
        self.assertEqual(branching["conditional_branch_count"], 2)

    def test_cpp_range_for_counts_as_loop(self) -> None:
        from engines.treesitter_engine import TreeSitterMathEngine

        source = (CPP_DIR / "nested_branchy.cpp").read_text()
        metrics = TreeSitterMathEngine("cpp").scan(source)[0].metrics
        self.assertEqual(metrics["max_loop_depth"], 2)
        self.assertEqual(metrics["loop_types"], ["for_range_loop", "for_range_loop"])

    def test_simple_c_is_low_complexity(self) -> None:
        from engines.treesitter_engine import TreeSitterBranchingEngine

        source = (C_DIR / "simple.c").read_text()
        self.assertEqual(TreeSitterBranchingEngine("c").scan(source)[0].metrics["cyclomatic_complexity"], 2)

    def test_unsafe_call_hazard_becomes_violation(self) -> None:
        source = (C_DIR / "unsafe.c").read_text()
        findings = EngineRegistry.default().findings_for(source, "c")
        kinds = {violation.kind for violation in validate_findings(findings).violations}
        self.assertIn("unsafe_call", kinds)


@unittest.skipUnless(TREE_SITTER, SKIP_REASON)
class TreeSitterRegistryAndControllerTests(unittest.TestCase):
    def test_registry_routes_c_and_cpp(self) -> None:
        registry = EngineRegistry.default()
        self.assertTrue(registry.has_language("c"))
        self.assertTrue(registry.has_language("cpp"))
        names = [engine.name for engine in registry.engines_for("c")]
        self.assertEqual(names, ["engine-1-math", "engine-2-hazards", "engine-3-branching"])

    def test_controller_flags_complex_c(self) -> None:
        source = (C_DIR / "nested_branchy.c").read_text()
        controller = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: source,
            policy={"max_cyclomatic_complexity": 4},
            language="c",
        )
        result = controller.run(target="c-nested", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "manual_review_required")
        kinds = [v["kind"] for v in result.payload["attempts"][0]["validation"]["violations"]]
        self.assertIn("cyclomatic_complexity", kinds)

    def test_controller_completes_clean_c(self) -> None:
        source = (C_DIR / "simple.c").read_text()
        controller = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: source,
            language="c",
        )
        result = controller.run(target="c-simple", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "completed")


class TemplateLibraryTests(unittest.TestCase):
    def test_lists_and_loads_snake_skeletons(self) -> None:
        library = TemplateLibrary()
        self.assertEqual(library.available("snake"), ["c", "cpp", "python"])
        for language in ("python", "c", "cpp"):
            self.assertIsNotNone(library.load("snake", language))

    def test_missing_template_returns_none(self) -> None:
        self.assertIsNone(TemplateLibrary().load("snake", "rust"))


@unittest.skipUnless(TREE_SITTER, SKIP_REASON)
class TemplateParsesTests(unittest.TestCase):
    def test_c_and_cpp_skeletons_parse_cleanly(self) -> None:
        library = TemplateLibrary()
        agent = ParseContractAgent()
        self.assertIsInstance(agent.parse(library.load("snake", "c"), language="c"), ParseSuccess)
        self.assertIsInstance(agent.parse(library.load("snake", "cpp"), language="cpp"), ParseSuccess)


class GatingContractTests(unittest.TestCase):
    """These must hold even when tree-sitter is absent."""

    def test_unknown_language_is_unsupported(self) -> None:
        result = ParseContractAgent().parse("fn main() {}", language="rust")
        self.assertIsInstance(result, ParseFailure)
        self.assertEqual(result.finding.summary, "Unsupported language")

    def test_empty_registry_returns_no_findings(self) -> None:
        self.assertEqual(EngineRegistry().findings_for("anything", "c"), [])

    def test_controller_gates_parseable_language_without_registered_engines(self) -> None:
        controller = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: "int main(void) { return 0; }\n",
            engine_registry=EngineRegistry(),
            language="c",
        )
        result = controller.run(target="empty-c-registry", initial_prompt="generate")
        self.assertEqual(result.payload["final_status"], "manual_review_required")
        violations = result.payload["attempts"][0]["validation"]["violations"]
        self.assertEqual(violations[0]["kind"], "parse_error")
        self.assertEqual(violations[0]["summary"], "Unsupported language")


if __name__ == "__main__":
    unittest.main()
