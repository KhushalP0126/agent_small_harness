"""Concrete typed wrappers around existing lint and execution behavior."""

from __future__ import annotations

import ast
import difflib
import fnmatch
import hashlib
import os
from dataclasses import asdict, dataclass, field, replace as dataclass_replace
from pathlib import Path

from agents.execution_agent import ExecutionAgent
from agents.repo_map_agent import DEFAULT_SKIP_DIRS
from backends.architect_client import ArchitectApiClient, ArchitectProfile
from backends.ollama_client import (
    DEFAULT_OLLAMA_MODEL,
    OllamaClient,
    OllamaGenerationConfig,
)
from engines.base import EngineFinding
from engines.lint_engine import LintEngine
from harness_kernel.container_sandbox import run_source_isolated
from harness_kernel.local_sandbox import MAX_CAPTURE_BYTES
from harness_kernel.tool_paths import repository_relative, resolve_within_root
from harness_kernel.tool_registry import ToolError, ToolHandler, ToolRegistry
from validation.behavior import (
    DEFAULT_BEHAVIOR_TIMEOUT_SECONDS,
    ExecutionTrace,
    FunctionBehaviorSpec,
)
from validation.deal_contracts import (
    serialize_deal_contract_result,
    validate_deal_examples,
)
from validation.formal import serialize_formal_result, validate_with_crosshair


@dataclass(frozen=True)
class LintRequest:
    source: str


@dataclass(frozen=True)
class LintResult:
    findings: list[EngineFinding] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(
            finding.severity in {"High", "Fatal"} for finding in self.findings
        )


@dataclass(frozen=True)
class ExecutionRequest:
    source: str
    spec: FunctionBehaviorSpec
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class OllamaGenerateRequest:
    prompt: str
    model: str = DEFAULT_OLLAMA_MODEL
    config: OllamaGenerationConfig | None = None
    system: str | None = None


@dataclass(frozen=True)
class ArchitectGenerateRequest:
    prompt: str
    system: str
    profile: ArchitectProfile | None = None


@dataclass(frozen=True)
class GenerateResponse:
    text: str


@dataclass(frozen=True)
class FormalVerificationRequest:
    source: str
    crosshair_enabled: bool = False
    timeout_seconds: float = 3.0


@dataclass(frozen=True)
class FormalVerificationResponse:
    result: dict


@dataclass(frozen=True)
class SearchDirectoryRequest:
    root: Path
    pattern: str
    max_results: int = 50


@dataclass(frozen=True)
class SearchDirectoryResponse:
    paths: list[str]
    truncated: bool = False


@dataclass(frozen=True)
class ReadFileRequest:
    root: Path
    path: str
    max_bytes: int = MAX_CAPTURE_BYTES


@dataclass(frozen=True)
class ReadFileResponse:
    path: str
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class ApplySearchReplaceRequest:
    root: Path
    path: str
    search: str
    replace: str
    operation: str = "replace"


@dataclass(frozen=True)
class ApplySearchReplaceResponse:
    path: str
    diff: str
    replacements: int
    proposed_content: str
    original_sha256: str
    operation: str = "replace"
    original_exists: bool = True
    applied: bool = False


@dataclass(frozen=True)
class CreateFileRequest:
    root: Path
    path: str
    content: str


@dataclass(frozen=True)
class CreateFileResponse:
    path: str
    diff: str
    proposed_content: str
    original_sha256: str
    applied: bool = False


@dataclass(frozen=True)
class MoveFileRequest:
    root: Path
    path: str
    destination: str


@dataclass(frozen=True)
class MoveFileResponse:
    path: str
    destination: str
    diff: str
    original_sha256: str
    applied: bool = False


@dataclass(frozen=True)
class CheckCodeRequest:
    root: Path
    path: str


@dataclass(frozen=True)
class CheckCodeResponse:
    path: str
    passed: bool
    findings: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class ExecuteScriptRequest:
    root: Path
    source: str
    timeout_seconds: float = 10.0
    language: str = "python"
    sandbox_mode: str = "container"
    runtime: str = "docker"


@dataclass(frozen=True)
class ExecuteScriptResponse:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool


SAFE_SCRIPT_IMPORTS = frozenset(
    {
        "collections",
        "dataclasses",
        "decimal",
        "fractions",
        "functools",
        "heapq",
        "itertools",
        "json",
        "math",
        "operator",
        "random",
        "re",
        "statistics",
        "string",
        "typing",
    }
)
BLOCKED_SCRIPT_CALLS = frozenset(
    {"__import__", "breakpoint", "compile", "eval", "exec", "input", "open"}
)


def _make_lint_handler(
    engine: LintEngine | None = None,
) -> ToolHandler[LintRequest, LintResult]:
    engine = engine or LintEngine()

    def invoke(request: LintRequest) -> LintResult:
        return LintResult(findings=engine.scan(request.source))

    return ToolHandler(
        name="lint",
        request_type=LintRequest,
        response_type=LintResult,
        invoke=invoke,
        description="Run pylint against a source string.",
    )


def _make_execution_sandbox_handler(
    agent: ExecutionAgent | None = None,
) -> ToolHandler[ExecutionRequest, ExecutionTrace]:
    agent = agent or ExecutionAgent(
        timeout_seconds=DEFAULT_BEHAVIOR_TIMEOUT_SECONDS
    )

    def invoke(request: ExecutionRequest) -> ExecutionTrace:
        return agent.execute(
            request.source,
            request.spec,
            timeout_seconds=request.timeout_seconds,
        )

    return ToolHandler(
        name="execution_sandbox",
        request_type=ExecutionRequest,
        response_type=ExecutionTrace,
        invoke=invoke,
        description="Execute behavior examples in the isolated draft sandbox.",
    )


def _make_ollama_generate_handler(
    client: OllamaClient | None = None,
) -> ToolHandler[OllamaGenerateRequest, GenerateResponse]:
    client = client or OllamaClient()

    def invoke(request: OllamaGenerateRequest) -> GenerateResponse:
        return GenerateResponse(
            client.generate(
                prompt=request.prompt,
                model=request.model,
                config=request.config,
                system=request.system,
            )
        )

    return ToolHandler(
        name="ollama_generate",
        request_type=OllamaGenerateRequest,
        response_type=GenerateResponse,
        invoke=invoke,
        description="Generate text through the configured local Ollama backend.",
    )


def _make_architect_generate_handler(
    client: ArchitectApiClient | None = None,
) -> ToolHandler[ArchitectGenerateRequest, GenerateResponse]:
    client = client or ArchitectApiClient()

    def invoke(request: ArchitectGenerateRequest) -> GenerateResponse:
        return GenerateResponse(
            client.generate(
                prompt=request.prompt,
                system=request.system,
                profile=request.profile,
            )
        )

    return ToolHandler(
        name="architect_generate",
        request_type=ArchitectGenerateRequest,
        response_type=GenerateResponse,
        invoke=invoke,
        description="Generate text through the configured architect backend.",
    )


def _make_formal_verification_handler(
) -> ToolHandler[FormalVerificationRequest, FormalVerificationResponse]:
    def invoke(request: FormalVerificationRequest) -> FormalVerificationResponse:
        deal_result = validate_deal_examples(
            request.source,
            timeout_seconds=request.timeout_seconds,
        )
        if not deal_result.is_compliant or not deal_result.skipped:
            result = serialize_deal_contract_result(deal_result)
            result["tool"] = "deal"
            return FormalVerificationResponse(result)
        if request.crosshair_enabled:
            return FormalVerificationResponse(
                serialize_formal_result(
                    validate_with_crosshair(
                        request.source,
                        timeout_seconds=request.timeout_seconds,
                    )
                )
            )
        return FormalVerificationResponse(
            {
                "is_compliant": True,
                "skipped": True,
                "tool": "formal",
                "issues": [],
            }
        )

    return ToolHandler(
        name="formal_verification",
        request_type=FormalVerificationRequest,
        response_type=FormalVerificationResponse,
        invoke=invoke,
        description="Run Deal examples and optional CrossHair verification.",
    )


def _make_search_directory_handler(
    repository_root: Path,
) -> ToolHandler[SearchDirectoryRequest, SearchDirectoryResponse]:
    def invoke(request: SearchDirectoryRequest) -> SearchDirectoryResponse:
        search_root = resolve_within_root(repository_root, request.root)
        if not search_root.is_dir():
            raise ToolError(f"Search root is not a directory: {request.root}", kind="not_directory")
        pattern = request.pattern.strip() or "*"
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise ToolError(f"Unsafe search pattern: {pattern!r}", kind="invalid_pattern")
        limit = max(1, min(int(request.max_results), 200))
        matches: list[str] = []
        truncated = False
        for current_root, dirnames, filenames in os.walk(search_root, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if name not in DEFAULT_SKIP_DIRS)
            for filename in sorted(filenames):
                candidate = resolve_within_root(repository_root, Path(current_root) / filename)
                relative_to_search = candidate.relative_to(search_root).as_posix()
                if not (
                    fnmatch.fnmatch(relative_to_search, pattern)
                    or fnmatch.fnmatch(filename, pattern)
                    or pattern in relative_to_search
                ):
                    continue
                if len(matches) >= limit:
                    truncated = True
                    return SearchDirectoryResponse(matches, truncated)
                matches.append(repository_relative(repository_root, candidate))
        return SearchDirectoryResponse(matches, truncated)

    return ToolHandler(
        name="search_directory",
        request_type=SearchDirectoryRequest,
        response_type=SearchDirectoryResponse,
        invoke=invoke,
        description="Search repository files by glob or substring without leaving the repository root.",
    )


def _make_read_file_handler(
    repository_root: Path,
) -> ToolHandler[ReadFileRequest, ReadFileResponse]:
    def invoke(request: ReadFileRequest) -> ReadFileResponse:
        requested = Path(request.path)
        path = resolve_within_root(
            repository_root,
            requested if requested.is_absolute() else request.root / requested,
        )
        if not path.is_file():
            raise ToolError(f"File does not exist: {request.path}", kind="not_file")
        limit = max(1, min(int(request.max_bytes), 1024 * 1024))
        payload = path.read_bytes()
        truncated = len(payload) > limit
        content = payload[:limit].decode("utf-8", errors="replace")
        if truncated:
            content += f"\n[truncated after {limit} bytes]\n"
        return ReadFileResponse(
            path=repository_relative(repository_root, path),
            content=content,
            truncated=truncated,
        )

    return ToolHandler(
        name="read_file",
        request_type=ReadFileRequest,
        response_type=ReadFileResponse,
        invoke=invoke,
        description="Read a repository file with bounded output and traversal protection.",
    )


def _make_apply_search_replace_handler(
    repository_root: Path,
) -> ToolHandler[ApplySearchReplaceRequest, ApplySearchReplaceResponse]:
    def invoke(request: ApplySearchReplaceRequest) -> ApplySearchReplaceResponse:
        requested = Path(request.path)
        path = resolve_within_root(
            repository_root,
            requested if requested.is_absolute() else request.root / requested,
        )
        operation = request.operation.casefold().strip() or "replace"
        if operation not in {"replace", "create", "delete"}:
            raise ToolError(
                f"Unsupported file operation: {request.operation}",
                kind="invalid_operation",
            )
        exists = path.exists()
        if exists and not path.is_file():
            raise ToolError(f"Path is not a regular file: {request.path}", kind="not_file")
        if operation == "create":
            if exists:
                raise ToolError(f"File already exists: {request.path}", kind="already_exists")
            original = ""
            proposed = request.replace
            replacements = 1
        else:
            if not path.is_file():
                raise ToolError(f"File does not exist: {request.path}", kind="not_file")
            original = path.read_text(encoding="utf-8")
            if operation == "delete":
                proposed = ""
                replacements = 1
            else:
                if not request.search:
                    raise ToolError("Search text cannot be empty", kind="invalid_search")
                replacements = original.count(request.search)
                if replacements == 0:
                    raise ToolError(
                        f"Search text was not found in {request.path}",
                        kind="search_not_found",
                    )
                proposed = original.replace(request.search, request.replace)
        relative = repository_relative(repository_root, path)
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        return ApplySearchReplaceResponse(
            path=relative,
            diff=diff,
            replacements=replacements,
            proposed_content=proposed,
            original_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
            operation=operation,
            original_exists=exists,
            applied=False,
        )

    return ToolHandler(
        name="apply_search_replace",
        request_type=ApplySearchReplaceRequest,
        response_type=ApplySearchReplaceResponse,
        invoke=invoke,
        description="Prepare a reviewed create, replace, or delete diff; never write repository files directly.",
    )


def _make_create_file_handler(
    repository_root: Path,
) -> ToolHandler[CreateFileRequest, CreateFileResponse]:
    def invoke(request: CreateFileRequest) -> CreateFileResponse:
        requested = Path(request.path)
        path = resolve_within_root(
            repository_root,
            requested if requested.is_absolute() else request.root / requested,
        )
        if path.exists():
            raise ToolError(f"File already exists: {request.path}", kind="already_exists")
        relative = repository_relative(repository_root, path)
        content = request.content
        diff = "".join(
            difflib.unified_diff(
                [],
                content.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        return CreateFileResponse(
            path=relative,
            diff=diff,
            proposed_content=content,
            original_sha256=hashlib.sha256(b"").hexdigest(),
            applied=False,
        )

    return ToolHandler(
        name="create_file",
        request_type=CreateFileRequest,
        response_type=CreateFileResponse,
        invoke=invoke,
        description="Prepare a new file for explicit review; never write until approved.",
    )


def apply_reviewed_create_file(
    repository_root: Path | str,
    proposal: CreateFileResponse,
    *,
    approved: bool,
) -> CreateFileResponse:
    """Apply a reviewed new-file proposal only after an explicit approval."""

    reviewed = ApplySearchReplaceResponse(
        path=proposal.path,
        diff=proposal.diff,
        replacements=1,
        proposed_content=proposal.proposed_content,
        original_sha256=proposal.original_sha256,
        operation="create",
        original_exists=False,
        applied=proposal.applied,
    )
    applied = apply_reviewed_search_replace(
        repository_root,
        reviewed,
        approved=approved,
    )
    return dataclass_replace(proposal, applied=applied.applied)


def _make_move_file_handler(
    repository_root: Path,
) -> ToolHandler[MoveFileRequest, MoveFileResponse]:
    def invoke(request: MoveFileRequest) -> MoveFileResponse:
        source_requested = Path(request.path)
        destination_requested = Path(request.destination)
        source = resolve_within_root(
            repository_root,
            source_requested if source_requested.is_absolute() else request.root / source_requested,
        )
        destination = resolve_within_root(
            repository_root,
            destination_requested
            if destination_requested.is_absolute()
            else request.root / destination_requested,
        )
        if not source.is_file():
            raise ToolError(f"File does not exist: {request.path}", kind="not_file")
        if destination.exists():
            raise ToolError(
                f"Destination already exists: {request.destination}",
                kind="destination_exists",
            )
        if source == destination:
            raise ToolError("Source and destination must differ", kind="same_path")
        content = source.read_text(encoding="utf-8")
        source_relative = repository_relative(repository_root, source)
        destination_relative = repository_relative(repository_root, destination)
        diff = "".join(
            [
                f"rename from {source_relative}\n",
                f"rename to {destination_relative}\n",
                f"--- a/{source_relative}\n",
                f"+++ b/{destination_relative}\n",
                *[
                    line
                    for line in difflib.unified_diff(
                        content.splitlines(keepends=True),
                        content.splitlines(keepends=True),
                        fromfile=f"a/{source_relative}",
                        tofile=f"b/{destination_relative}",
                    )
                    if not line.startswith("---") and not line.startswith("+++")
                ],
            ]
        )
        return MoveFileResponse(
            path=source_relative,
            destination=destination_relative,
            diff=diff,
            original_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    return ToolHandler(
        name="move_file",
        request_type=MoveFileRequest,
        response_type=MoveFileResponse,
        invoke=invoke,
        description="Prepare a reviewed repository-safe file move or rename.",
    )


def apply_reviewed_move_file(
    repository_root: Path | str,
    proposal: MoveFileResponse,
    *,
    approved: bool,
) -> MoveFileResponse:
    """Move an unchanged reviewed file only after explicit approval."""

    if not approved:
        return proposal
    root = Path(repository_root).resolve()
    source = resolve_within_root(root, proposal.path)
    destination = resolve_within_root(root, proposal.destination)
    if not source.is_file():
        raise ToolError(f"File does not exist: {proposal.path}", kind="not_file")
    if destination.exists():
        raise ToolError(
            f"Destination appeared after review: {proposal.destination}",
            kind="stale_diff",
        )
    current_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if current_sha256 != proposal.original_sha256:
        raise ToolError(
            f"File changed after review: {proposal.path}",
            kind="stale_diff",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    return dataclass_replace(proposal, applied=True)


def _make_check_code_handler(
    repository_root: Path,
) -> ToolHandler[CheckCodeRequest, CheckCodeResponse]:
    def invoke(request: CheckCodeRequest) -> CheckCodeResponse:
        requested = Path(request.path)
        path = resolve_within_root(
            repository_root,
            requested if requested.is_absolute() else request.root / requested,
        )
        if not path.is_file():
            raise ToolError(f"File does not exist: {request.path}", kind="not_file")
        language = {".py": "python", ".c": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp"}.get(
            path.suffix.casefold()
        )
        if language is None:
            raise ToolError(
                f"No structural checker is registered for: {request.path}",
                kind="unsupported_language",
            )
        from agents.engine_registry import EngineRegistry

        source = path.read_text(encoding="utf-8")
        findings = EngineRegistry.default().findings_for(source, language)
        payload = [asdict(finding) for finding in findings]
        return CheckCodeResponse(
            path=repository_relative(repository_root, path),
            passed=not any(finding.severity in {"High", "Fatal"} for finding in findings),
            findings=payload,
        )

    return ToolHandler(
        name="check_code",
        request_type=CheckCodeRequest,
        response_type=CheckCodeResponse,
        invoke=invoke,
        description="Run the registered structural and lint checks for one repository file.",
    )


def apply_reviewed_search_replace(
    repository_root: Path | str,
    proposal: ApplySearchReplaceResponse,
    *,
    approved: bool,
) -> ApplySearchReplaceResponse:
    """Apply an unchanged proposal only after an explicit host/UI approval."""

    if not approved:
        return proposal
    root = Path(repository_root).resolve()
    path = resolve_within_root(root, proposal.path)
    if proposal.original_exists:
        if not path.is_file():
            raise ToolError(f"File does not exist: {proposal.path}", kind="not_file")
        current = path.read_text(encoding="utf-8")
        current_sha256 = hashlib.sha256(current.encode("utf-8")).hexdigest()
        if current_sha256 != proposal.original_sha256:
            raise ToolError(
                f"File changed after diff review: {proposal.path}",
                kind="stale_diff",
            )
    elif path.exists():
        raise ToolError(f"File appeared after diff review: {proposal.path}", kind="stale_diff")

    if proposal.operation == "delete":
        path.unlink()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(proposal.proposed_content, encoding="utf-8")
    return dataclass_replace(proposal, applied=True)


def _make_execute_script_handler(
    repository_root: Path,
    *,
    allow_local_sandbox: bool = False,
) -> ToolHandler[ExecuteScriptRequest, ExecuteScriptResponse]:
    def invoke(request: ExecuteScriptRequest) -> ExecuteScriptResponse:
        resolve_within_root(repository_root, request.root)
        if request.sandbox_mode == "local" and not allow_local_sandbox:
            raise ToolError(
                "Local script execution is disabled for registered tools; use the container policy",
                kind="local_sandbox_disabled",
            )
        if not request.source.strip():
            raise ToolError("Script source cannot be empty", kind="invalid_source")
        if len(request.source.encode("utf-8")) > 128 * 1024:
            raise ToolError("Script source exceeds 128 KiB", kind="source_too_large")
        if request.language.casefold() in {"python", "python3", "py"}:
            _validate_tool_script(request.source)
        timeout = max(0.1, min(float(request.timeout_seconds), 30.0))
        result = run_source_isolated(
            request.source,
            request.language,
            timeout_seconds=timeout,
            mode=request.sandbox_mode,
            runtime=request.runtime,
        )
        return ExecuteScriptResponse(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
        )

    return ToolHandler(
        name="execute_script",
        request_type=ExecuteScriptRequest,
        response_type=ExecuteScriptResponse,
        invoke=invoke,
        description="Execute generated source in a disposable Docker sandbox with no network by default.",
    )


def _validate_tool_script(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ToolError(f"Script does not parse: {exc}", kind="invalid_source") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name.split(".", 1)[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [(node.module or "").split(".", 1)[0]]
        else:
            modules = []
        blocked_modules = [module for module in modules if module not in SAFE_SCRIPT_IMPORTS]
        if blocked_modules:
            raise ToolError(
                f"Script import is not allowed: {', '.join(blocked_modules)}",
                kind="unsafe_script",
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in BLOCKED_SCRIPT_CALLS
        ):
            raise ToolError(
                f"Script call is not allowed: {node.func.id}",
                kind="unsafe_script",
            )


def build_default_tool_registry(
    lint_engine: LintEngine | None = None,
    execution_agent: ExecutionAgent | None = None,
    ollama_client: OllamaClient | None = None,
    architect_client: ArchitectApiClient | None = None,
    repository_root: Path | str | None = None,
    allow_local_sandbox: bool = False,
) -> ToolRegistry:
    trusted_root = Path(repository_root or Path.cwd()).resolve()
    registry = ToolRegistry()
    registry.register(_make_lint_handler(lint_engine))
    registry.register(_make_execution_sandbox_handler(execution_agent))
    registry.register(_make_ollama_generate_handler(ollama_client))
    registry.register(_make_architect_generate_handler(architect_client))
    registry.register(_make_formal_verification_handler())
    registry.register(_make_search_directory_handler(trusted_root))
    registry.register(_make_read_file_handler(trusted_root))
    registry.register(_make_apply_search_replace_handler(trusted_root))
    registry.register(_make_create_file_handler(trusted_root))
    registry.register(_make_move_file_handler(trusted_root))
    registry.register(_make_check_code_handler(trusted_root))
    registry.register(
        _make_execute_script_handler(
            trusted_root,
            allow_local_sandbox=allow_local_sandbox,
        )
    )
    return registry
