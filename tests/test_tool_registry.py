import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.engine_registry import EngineRegistry
from agents.generation_controller import GenerationController
from engines.base import EngineFinding
from harness_kernel.tool_handlers import (
    ArchitectGenerateRequest,
    ApplySearchReplaceRequest,
    ApplySearchReplaceResponse,
    ExecuteScriptRequest,
    ExecuteScriptResponse,
    ExecutionRequest,
    FormalVerificationRequest,
    FormalVerificationResponse,
    GenerateResponse,
    LintRequest,
    LintResult,
    OllamaGenerateRequest,
    ReadFileRequest,
    ReadFileResponse,
    SearchDirectoryRequest,
    SearchDirectoryResponse,
    apply_reviewed_search_replace,
    build_default_tool_registry,
)
from harness_kernel.tool_registry import ToolError, ToolHandler, ToolRegistry
from validation.behavior import BehaviorCase, FunctionBehaviorSpec


@dataclass(frozen=True)
class EchoRequest:
    value: int


@dataclass(frozen=True)
class EchoResponse:
    value: int


class ToolRegistryTests(unittest.TestCase):
    def test_dispatch_success(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolHandler(
                name="echo",
                request_type=EchoRequest,
                response_type=EchoResponse,
                invoke=lambda request: EchoResponse(request.value + 1),
            )
        )
        result = registry.dispatch("echo", EchoRequest(1))
        self.assertTrue(result.ok)
        self.assertEqual(result.value, EchoResponse(2))

    def test_unknown_tool_and_wrong_request_are_typed_failures(self) -> None:
        registry = ToolRegistry()
        self.assertEqual(
            registry.dispatch("missing", EchoRequest(1)).error_kind,
            "unknown_tool",
        )
        registry.register(
            ToolHandler("echo", EchoRequest, EchoResponse, lambda request: EchoResponse(request.value))
        )
        self.assertEqual(
            registry.dispatch("echo", "wrong").error_kind,
            "invalid_request_type",
        )

    def test_handler_failures_do_not_escape_dispatch(self) -> None:
        registry = ToolRegistry()

        def fail(_request: EchoRequest) -> EchoResponse:
            raise RuntimeError("kaboom")

        registry.register(ToolHandler("echo", EchoRequest, EchoResponse, fail))
        result = registry.dispatch("echo", EchoRequest(1))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "handler_exception")
        self.assertIn("kaboom", result.error)

    def test_typed_error_preserves_kind(self) -> None:
        registry = ToolRegistry()

        def fail(_request: EchoRequest) -> EchoResponse:
            raise ToolError("bad input", kind="validation_failed")

        registry.register(ToolHandler("echo", EchoRequest, EchoResponse, fail))
        self.assertEqual(
            registry.dispatch("echo", EchoRequest(1)).error_kind,
            "validation_failed",
        )

    def test_duplicate_registration_is_rejected(self) -> None:
        registry = ToolRegistry()
        handler = ToolHandler(
            "echo", EchoRequest, EchoResponse, lambda request: EchoResponse(request.value)
        )
        registry.register(handler)
        with self.assertRaises(ValueError):
            registry.register(handler)

    def test_default_handlers_dispatch(self) -> None:
        registry = build_default_tool_registry()
        lint_result = registry.dispatch("lint", LintRequest("x = 1\n"))
        self.assertTrue(lint_result.ok)
        self.assertIsInstance(lint_result.value, LintResult)

        spec = FunctionBehaviorSpec(
            function_name="add",
            cases=[BehaviorCase(name="basic", args=(2, 3), kwargs={}, expected=5)],
        )
        execution = registry.dispatch(
            "execution_sandbox",
            ExecutionRequest("def add(a, b):\n    return a + b\n", spec),
        )
        self.assertTrue(execution.ok)
        self.assertTrue(execution.value.cases[0].matched)
        formal = registry.dispatch(
            "formal_verification",
            FormalVerificationRequest("def add(a, b):\n    return a + b\n"),
        )
        self.assertTrue(formal.ok)
        self.assertIsInstance(formal.value, FormalVerificationResponse)
        self.assertTrue(formal.value.result["is_compliant"])
        self.assertTrue(formal.value.result["skipped"])

    def test_model_handlers_return_typed_responses(self) -> None:
        class OllamaStub:
            def generate(self, **_kwargs):
                return "ollama-result"

        class ArchitectStub:
            def generate(self, **_kwargs):
                return "architect-result"

        registry = build_default_tool_registry(
            ollama_client=OllamaStub(),
            architect_client=ArchitectStub(),
        )
        ollama = registry.dispatch(
            "ollama_generate",
            OllamaGenerateRequest(prompt="generate"),
        )
        architect = registry.dispatch(
            "architect_generate",
            ArchitectGenerateRequest(prompt="repair", system="code only"),
        )

        self.assertEqual(ollama.value, GenerateResponse("ollama-result"))
        self.assertEqual(architect.value, GenerateResponse("architect-result"))

    def test_repository_tools_search_read_diff_and_execute_without_writing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / "src" / "example.py"
            source_path.parent.mkdir()
            source_path.write_text("value = 1\n", encoding="utf-8")
            registry = build_default_tool_registry(repository_root=root)

            search = registry.dispatch(
                "search_directory",
                SearchDirectoryRequest(root=Path("."), pattern="*.py"),
            )
            read = registry.dispatch(
                "read_file",
                ReadFileRequest(root=Path("src"), path="example.py"),
            )
            replacement = registry.dispatch(
                "apply_search_replace",
                ApplySearchReplaceRequest(
                    root=Path("."),
                    path="src/example.py",
                    search="value = 1",
                    replace="value = 2",
                ),
            )
            execution = registry.dispatch(
                "execute_script",
                ExecuteScriptRequest(root=Path("."), source="print('sandboxed')"),
            )

            self.assertEqual(search.value, SearchDirectoryResponse(["src/example.py"], False))
            self.assertEqual(read.value, ReadFileResponse("src/example.py", "value = 1\n", False))
            self.assertIsInstance(replacement.value, ApplySearchReplaceResponse)
            self.assertIn("+value = 2", replacement.value.diff)
            self.assertFalse(replacement.value.applied)
            self.assertEqual(source_path.read_text(encoding="utf-8"), "value = 1\n")
            declined = apply_reviewed_search_replace(
                root,
                replacement.value,
                approved=False,
            )
            self.assertFalse(declined.applied)
            self.assertEqual(source_path.read_text(encoding="utf-8"), "value = 1\n")
            accepted = apply_reviewed_search_replace(
                root,
                replacement.value,
                approved=True,
            )
            self.assertTrue(accepted.applied)
            self.assertEqual(source_path.read_text(encoding="utf-8"), "value = 2\n")
            self.assertIsInstance(execution.value, ExecuteScriptResponse)
            self.assertEqual(execution.value.stdout.strip(), "sandboxed")

            unsafe = registry.dispatch(
                "execute_script",
                ExecuteScriptRequest(
                    root=Path("."),
                    source="print(open('/etc/passwd').read())",
                ),
            )
            self.assertFalse(unsafe.ok)
            self.assertEqual(unsafe.error_kind, "unsafe_script")

    def test_repository_tools_reject_path_and_symlink_escape(self) -> None:
        with TemporaryDirectory() as tmpdir, TemporaryDirectory() as outside_dir:
            root = Path(tmpdir)
            outside = Path(outside_dir) / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            registry = build_default_tool_registry(repository_root=root)

            traversal = registry.dispatch(
                "read_file",
                ReadFileRequest(root=Path("."), path="../secret.txt"),
            )
            absolute = registry.dispatch(
                "read_file",
                ReadFileRequest(root=Path("."), path=str(outside)),
            )
            self.assertEqual(traversal.error_kind, "path_escape")
            self.assertEqual(absolute.error_kind, "path_escape")

            link = root / "outside-link"
            try:
                link.symlink_to(outside)
            except OSError:
                return
            symlink = registry.dispatch(
                "read_file",
                ReadFileRequest(root=Path("."), path="outside-link"),
            )
            self.assertEqual(symlink.error_kind, "path_escape")

    def test_reviewed_diff_rejects_stale_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "example.py"
            path.write_text("value = 1\n", encoding="utf-8")
            registry = build_default_tool_registry(repository_root=root)
            result = registry.dispatch(
                "apply_search_replace",
                ApplySearchReplaceRequest(
                    root=Path("."),
                    path="example.py",
                    search="value = 1",
                    replace="value = 2",
                ),
            )
            path.write_text("value = 3\n", encoding="utf-8")

            with self.assertRaises(ToolError) as raised:
                apply_reviewed_search_replace(root, result.value, approved=True)

            self.assertEqual(raised.exception.kind, "stale_diff")
            self.assertEqual(path.read_text(encoding="utf-8"), "value = 3\n")

    def test_controller_surfaces_registered_formal_handler_failure(self) -> None:
        registry = ToolRegistry()

        def fail(
            _request: FormalVerificationRequest,
        ) -> FormalVerificationResponse:
            raise RuntimeError("verifier unavailable")

        registry.register(
            ToolHandler(
                "formal_verification",
                FormalVerificationRequest,
                FormalVerificationResponse,
                fail,
            )
        )
        controller = GenerationController(max_retries=0, tool_registry=registry)

        result = controller._validate_formal_contracts("def identity(value):\n    return value\n")

        self.assertFalse(result["is_compliant"])
        self.assertFalse(result["skipped"])
        self.assertEqual(result["tool"], "formal_verification")
        self.assertIn("verifier unavailable", result["issues"][0]["details"])

    def test_model_handler_failure_is_contained(self) -> None:
        class BrokenOllama:
            def generate(self, **_kwargs):
                raise TimeoutError("timed out")

        registry = build_default_tool_registry(ollama_client=BrokenOllama())
        result = registry.dispatch(
            "ollama_generate",
            OllamaGenerateRequest(prompt="generate"),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "handler_exception")
        self.assertIn("timed out", result.error)

    def test_engine_registry_routes_lint_through_tool_registry(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolHandler(
                name="lint",
                request_type=LintRequest,
                response_type=LintResult,
                invoke=lambda _request: LintResult(
                    [
                        EngineFinding(
                            engine="engine-lint",
                            severity="High",
                            summary="dispatched lint finding",
                            details="lint handler was invoked",
                        )
                    ]
                ),
            )
        )

        findings = EngineRegistry.default(tool_registry=registry).findings_for(
            "def clean(value):\n    return value\n",
            "python",
        )
        self.assertTrue(
            any(finding.summary == "dispatched lint finding" for finding in findings)
        )


if __name__ == "__main__":
    unittest.main()
