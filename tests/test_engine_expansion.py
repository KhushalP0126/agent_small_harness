import shutil
import json
import tempfile
import unittest
from pathlib import Path

from agents.artifact_manager import ArtifactManager
from agents.engine_registry import EngineRegistry
from agents.generation_controller import GenerationController
from engines import treesitter_support
from engines.base import EngineFinding
from engines.compilation_engine import CompilationEngine
from harness_kernel.compute_shield import (
    ShieldTaskTokens,
    compute_shield_metrics,
    shield_task_from_artifacts,
)
from harness_kernel.profiling import AlgorithmicProfiler, ProfileResult


class CompilationEngineTests(unittest.TestCase):
    def test_registry_always_exposes_compilation_gate_for_supported_non_python_languages(self) -> None:
        registry = EngineRegistry.default()
        for language in ("c", "cpp", "rust", "javascript"):
            self.assertTrue(registry.has_language(language))
            self.assertEqual(
                registry.engines_for(language)[0].name,
                "engine-compilation",
            )

    @unittest.skipUnless(shutil.which("clang") or shutil.which("gcc"), "C compiler unavailable")
    def test_valid_c_passes(self) -> None:
        findings = CompilationEngine("c").scan(
            "int add(int a, int b) { return a + b; }\n"
        )
        self.assertEqual(findings, [])

    @unittest.skipUnless(shutil.which("clang") or shutil.which("gcc"), "C compiler unavailable")
    def test_syntax_error_fails_with_event_ready_metrics(self) -> None:
        finding = CompilationEngine("c").scan("int main(void) { return ; nope }\n")[0]
        self.assertEqual(finding.severity, "High")
        self.assertEqual(finding.metrics["compile_status"], "fail")
        self.assertTrue(finding.metrics["errors"])

    @unittest.skipUnless(shutil.which("clang") or shutil.which("gcc"), "C compiler unavailable")
    def test_warning_is_strict_failure(self) -> None:
        finding = CompilationEngine("c").scan(
            "int main(void) { int unused = 1; return 0; }\n"
        )[0]
        self.assertEqual(finding.metrics["compile_status"], "fail")

    @unittest.skipUnless(
        shutil.which("clang++") or shutil.which("g++"),
        "C++ compiler unavailable",
    )
    def test_valid_cpp_passes(self) -> None:
        findings = CompilationEngine("cpp").scan(
            "int add(int a, int b) { return a + b; }\n"
        )
        self.assertEqual(findings, [])

    @unittest.skipUnless(shutil.which("rustc"), "rustc unavailable")
    def test_valid_rust_passes(self) -> None:
        self.assertEqual(CompilationEngine("rust").scan("pub fn add(a: i32, b: i32) -> i32 { a + b }\n"), [])

    @unittest.skipUnless(shutil.which("rustc"), "rustc unavailable")
    def test_invalid_rust_fails(self) -> None:
        finding = CompilationEngine("rust").scan("pub fn broken( {\n")[0]
        self.assertEqual(finding.metrics["compile_status"], "fail")

    @unittest.skipUnless(shutil.which("node"), "node unavailable")
    def test_valid_javascript_passes(self) -> None:
        self.assertEqual(CompilationEngine("javascript").scan("export function add(a, b) { return a + b; }\n"), [])

    @unittest.skipUnless(shutil.which("node"), "node unavailable")
    def test_invalid_javascript_fails(self) -> None:
        finding = CompilationEngine("javascript").scan("function broken( {\n")[0]
        self.assertEqual(finding.metrics["compile_status"], "fail")

    @unittest.skipUnless(
        treesitter_support.is_available()
        and (shutil.which("clang") or shutil.which("gcc")),
        "C parser/compiler unavailable",
    )
    def test_controller_emits_compile_event_during_normal_scan(self) -> None:
        events = []
        session = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: "int main(void) { return 0; }\n",
            language="c",
            event_sink=events.append,
        ).run(target="c", initial_prompt="generate").payload
        self.assertEqual(session["final_status"], "completed")
        self.assertEqual(
            events[0],
            {"type": "compile_gate_result", "status": "pass", "errors": []},
        )


class AlgorithmicProfilerTests(unittest.TestCase):
    def test_measure_reports_median_and_spread(self) -> None:
        profiler = AlgorithmicProfiler(repeats=3, warmups=0)
        result = profiler.measure("MKN", lambda: sum(range(10)))
        self.assertEqual(result.loop_order, "MKN")
        self.assertEqual(len(result.samples_ns), 3)
        self.assertGreaterEqual(result.runtime_ns, 0)
        self.assertGreaterEqual(result.spread_ns, 0)

    def test_noise_floor_avoids_false_winner(self) -> None:
        first = ProfileResult("MKN", 100, 2, None, (99, 100, 101))
        second = ProfileResult("NKM", 102, 2, None, (101, 102, 103))
        self.assertIsNone(
            AlgorithmicProfiler.faster(first, second, minimum_margin=0.05)
        )

    def test_clear_winner_is_selected(self) -> None:
        first = ProfileResult("MKN", 100, 2, None, (99, 100, 101))
        second = ProfileResult("NKM", 200, 2, None, (199, 200, 201))
        self.assertEqual(AlgorithmicProfiler.faster(first, second), first)

    def test_compare_flags_selected_slower_order(self) -> None:
        class FixedProfiler(AlgorithmicProfiler):
            def measure(self, loop_order, operation, **kwargs):
                del operation, kwargs
                runtime = 100 if loop_order == "MKN" else 200
                return ProfileResult(
                    loop_order,
                    runtime,
                    2,
                    None,
                    (runtime - 1, runtime, runtime + 1),
                )

        _, findings = FixedProfiler().compare(
            "MKN",
            lambda: None,
            "NKM",
            lambda: None,
            selected_order="NKM",
        )
        self.assertEqual(findings[0].metrics["faster_order"], "MKN")

    def test_controller_records_and_gates_opt_in_profile(self) -> None:
        result_rows = [
            ProfileResult("MKN", 100, 2, None, (99, 100, 101)),
            ProfileResult("NKM", 200, 2, None, (199, 200, 201)),
        ]

        def runner(_source):
            return result_rows, [
                EngineFinding(
                    engine="engine-algorithmic-profiling",
                    severity="High",
                    summary="Selected implementation is measurably slower",
                    details="NKM median 200ns; MKN median 100ns.",
                    metrics={
                        "selected_runtime_ns": 200,
                        "faster_runtime_ns": 100,
                    },
                )
            ]

        events = []
        controller = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: "def run():\n    return 1\n",
            profiling_runner=runner,
            event_sink=events.append,
        )
        session = controller.run(target="profile", initial_prompt="generate").payload
        self.assertEqual(session["final_status"], "manual_review_required")
        profile = session["attempts"][0]["profiling_validation"]
        self.assertTrue(profile["enabled"])
        self.assertFalse(profile["is_compliant"])
        self.assertEqual(len(profile["results"]), 2)
        self.assertEqual(
            [event["type"] for event in events],
            ["profiling_result", "profiling_result"],
        )

    def test_controller_default_does_not_profile(self) -> None:
        session = GenerationController(
            max_retries=0,
            draft_supplier=lambda _prompt: "def run():\n    return 1\n",
        ).run(target="plain", initial_prompt="generate").payload
        profile = session["attempts"][0]["profiling_validation"]
        self.assertFalse(profile["enabled"])
        self.assertTrue(profile["is_compliant"])


class ComputeShieldTests(unittest.TestCase):
    def test_aggregate_keeps_raw_task_evidence(self) -> None:
        metrics = compute_shield_metrics(
            [
                ShieldTaskTokens("one", 100, 40),
                ShieldTaskTokens("two", 80, 60),
            ]
        )
        self.assertEqual(metrics.tokens_baseline, 180)
        self.assertEqual(metrics.tokens_shielded, 100)
        self.assertEqual(metrics.delta, 80)
        self.assertEqual([row.task for row in metrics.tasks], ["one", "two"])

    def test_negative_token_count_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compute_shield_metrics([ShieldTaskTokens("bad", -1, 0)])

    def test_artifact_telemetry_builds_task_row(self) -> None:
        row = shield_task_from_artifacts(
            "matrix",
            {"telemetry": {"model_calls": [{"total_tokens": 120}]}},
            {
                "telemetry": {
                    "model_calls": [
                        {"total_tokens": 30},
                        {"total_tokens": 20},
                    ]
                }
            },
        )
        self.assertEqual(row, ShieldTaskTokens("matrix", 120, 50))

    def test_artifact_manager_persists_profile_and_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ArtifactManager(tmp)
            paths = manager.create_run("connected")
            manager.save_session(
                {
                    "attempts": [
                        {
                            "attempt": 0,
                            "draft": "def run():\n    return 1\n",
                            "validation": {"is_compliant": True, "violations": []},
                            "behavior_validation": {
                                "is_compliant": True,
                                "issues": [],
                            },
                            "execution_trace": {"cases": [{"returned": "1"}]},
                            "profiling_validation": {
                                "enabled": True,
                                "is_compliant": True,
                                "results": [{"loop_order": "MKN"}],
                                "issues": [],
                            },
                            "formal_validation": {
                                "is_compliant": True,
                                "issues": [],
                            },
                        }
                    ]
                },
                paths,
            )
            validation = json.loads(
                (Path(tmp) / "connected" / "attempt_0_validation.json").read_text()
            )
            timeline = json.loads(
                (Path(tmp) / "connected" / "attempt_timeline.json").read_text()
            )
            self.assertEqual(
                validation["execution_trace"]["cases"][0]["returned"], "1"
            )
            self.assertTrue(validation["profiling_validation"]["enabled"])
            self.assertTrue(timeline[0]["profiling_enabled"])


if __name__ == "__main__":
    unittest.main()
