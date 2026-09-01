"""JSON-lines subprocess bridge for the Rust TUI.

The bridge deliberately launches existing CLI entrypoints instead of importing
``GenerationController``. JSON is reserved for stdout; child output is wrapped
as structured log events so malformed human-readable lines cannot corrupt the
protocol.
"""

from __future__ import annotations

import ast
import json
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
import threading
import httpx
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

from agents.artifact_manager import ArtifactManager
from agents.repo_map_agent import RepoMapAgent
from agents.tool_calling_agent import ToolCallRecord, ToolCallingAgent
from backends.architect_client import (
    DEFAULT_ARCHITECT_API_BASE_URL,
    DEFAULT_ARCHITECT_MODEL,
    ArchitectApiClient,
    ArchitectConfig,
)
from backends.ollama_client import (
    DEFAULT_OLLAMA_MODEL,
    OllamaClient,
    OllamaGenerationConfig,
)
from engines.compilation_engine import CompilationEngine
from harness_kernel.compute_shield import ShieldTaskTokens, compute_shield_metrics
from harness_kernel.profiling import ProfileResult
from harness_kernel.contribution import ApiCostGuard, ContributionSplit, ContributionTelemetry, WeightedSchedule
from harness_kernel.provider_settings import (
    Provider, ProviderSettings, SessionCredentialStore, credential_metadata,
    default_credential_store, resolve_credential,
)
from harness_kernel.research_readiness import evaluate_research_readiness
from harness_kernel.event_stream import EVENT_FD_ENV
from harness_kernel.tool_handlers import (
    ApplySearchReplaceResponse,
    CheckCodeRequest,
    CheckCodeResponse,
    CreateFileResponse,
    MoveFileResponse,
    ReadFileResponse,
    apply_reviewed_create_file,
    apply_reviewed_move_file,
    apply_reviewed_search_replace,
    build_default_tool_registry,
)
from harness_kernel.tool_paths import resolve_within_root
from harness_kernel.checkpoints import CheckpointStore
from harness_kernel.extensions import ExtensionManifest, register_extension_tools
from harness_kernel.governance import PermissionEvaluator, PermissionMode
from harness_kernel.language_adapters import detect_project
from harness_kernel.orchestration_store import OrchestrationStore
from harness_kernel.task_graph import ProviderPolicy, TaskGraph, TaskNode
from TUI.repo_renderer import render_repo_architecture


PROTOCOL_VERSION = 7
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY_PATH = REPO_ROOT / ".tui_memory.json"
MAX_CHAT_MESSAGES = 24
MAX_PREFERENCES = 50
MAX_QUESTIONS = 4
MAX_OPTIONS_PER_QUESTION = 5
ARCHITECT_CONTEXT_WINDOW = 65_536
LOCAL_CONTEXT_WINDOW = 8_192
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30s")
COST_WARNING_THRESHOLD_USD = 1.00
COMPACTION_TRIGGER_RATIO = 0.6
RESEARCH_DIR = REPO_ROOT / "docs" / "research"
RESEARCH_SYSTEM_PROMPT = """You are a codebase research analyst.
Given tool-call findings gathered from the repository and the user's question, produce a compact research artifact. Do not propose a plan and do not write code.
Return JSON only, with exactly this shape:
{"question":"...","relevant_files":["path: reason"],"code_flow":["ordered step"],"hypothesis":"...","open_questions":["..."]}
Ground every claim in the supplied findings; use empty lists or an empty string when inconclusive."""
COMPACTION_SYSTEM_PROMPT = """You are compacting a coding-agent conversation before its context window fills.
Return JSON only, with exactly this shape:
{"goal":"...","approach":"...","done":["..."],"current_blocker":"...","key_facts":["..."],"active_diffs":["..."],"contribution_state":"...","cost_state":"...","checkpoint_ids":["..."]}
Preserve concrete decisions, completed work, constraints, file paths, active diffs, provider contribution, API cost state, and checkpoints. Use empty lists or an empty string where needed."""
QUESTIONNAIRE_SYSTEM_PROMPT = """You are an autonomous planning worker and software architect.
The user has explicitly asked to plan, build, create, design, implement, or change software. Do not write code and do not claim to execute anything. Return JSON only in this shape:
{"kind":"questionnaire","message":"short introduction","questions":[{"question_text":"...","options":["...","..."]}]}
Ask 2 to 4 high-impact clarification questions. Give each question 2 to 4 concise, mutually distinct choices. Do not include Other; the application adds it. Focus on behavior, scope, constraints, data, interfaces, and acceptance criteria.
Never return markdown fences around the JSON. The application creates a formal spec only after the questionnaire is completed or the user explicitly requests a draft."""
CHAT_SYSTEM_PROMPT = """You are a concise, helpful coding assistant.
Answer the user's latest message directly. Do not emit tool-call JSON, do not claim to have changed files, and do not open a planning questionnaire unless the user explicitly asked to plan software."""
CODE_DRAFT_SYSTEM_PROMPT = """You are a coding assistant preparing a small, safe, reviewable repository change set.
Return exactly one repository tool-call JSON object per turn. First inspect relevant files when the task modifies existing code. Then propose up to four independent changes:
- create_file: {"action":"tool","tool":"create_file","arguments":{"path":"relative/path","content":"full source"}}
- apply_search_replace: {"action":"tool","tool":"apply_search_replace","arguments":{"path":"relative/path","search":"exact existing text","replace":"replacement text"}}

Generate real, complete code for the user's request. Never write files directly and never use markdown fences. Every proposed diff is reviewed by the user before it is applied. When enough reviewed changes are prepared, return {"action":"final","answer":"<concise implementation summary>"}. If the request cannot be made safely, return one concise clarification question."""
SPEC_SHEET_SYSTEM_PROMPT = """You are a software specification architect.
Fill out the supplied execution spec sheet from the conversation and questionnaire answers.
Return JSON only, with no markdown fence and no commentary, using exactly this shape:
{
  "app_spec": {"name":"snake_game","language":"python","libraries":[],"kernel_mode":"generate_from_spec"},
  "goal":"one concrete implementation goal",
  "files":["relative/path.py"],
  "required_components":["GameConfig","SnakeState","SnakeGame","main()"],
  "entrypoints":["main()"],
  "dependency_graph":["GameConfig -> SnakeState","SnakeState -> SnakeGame","SnakeGame -> main"],
  "state_rules":["explicit state transition rule"],
  "interfaces":["input or output interface"],
  "constraints":["implementation constraint"],
  "acceptance_examples":["concrete observable example"],
  "validation":["command or check proving acceptance"]
}
Every field is required. Use identifier-only names in required_components and entrypoints; a function may end in (). Use relative paths only. The dependency graph must use only required component or entrypoint names, with prerequisites on the left and dependents on the right. Preserve every user answer. Make the sheet sufficiently concrete for another worker to implement without asking follow-up questions. Do not implement the project."""
ENTRYPOINTS = {
    "coding_capability": REPO_ROOT / "scripts" / "run_coding_capability.py",
    "worker_limit": REPO_ROOT / "scripts" / "run_worker_limit.py",
    "structured_spec": REPO_ROOT / "scripts" / "run_structured_spec.py",
}


@dataclass(frozen=True)
class PendingActionApproval:
    text: str
    reason: str
    route: str
    provider: str = "deepseek"
ALLOWED_FLAGS = {
    "--artifact-root",
    "--architect-after-repair-attempts",
    "--config",
    "--decompositions",
    "--history",
    "--max-retries",
    "--model",
    "--resume-run",
    "--runs",
    "--save-artifacts",
    "--spec",
    "--tasks",
}


class EventWriter:
    def __init__(self, stream: TextIO = sys.stdout) -> None:
        self.stream = stream
        self._lock = threading.Lock()

    def emit(self, event_type: str, **payload: Any) -> None:
        self.emit_event({"type": event_type, **payload})

    def emit_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.stream.write(
                json.dumps(event, separators=(",", ":")) + "\n"
            )
            self.stream.flush()


class Bridge:
    def __init__(
        self,
        writer: EventWriter | None = None,
        artifact_root: Path | str | None = None,
        env_file: Path | str | None = None,
        memory_path: Path | str | None = None,
        architect_client: ArchitectApiClient | None = None,
        tool_generate_text: Any = None,
        tool_repository_root: Path | str | None = None,
        credential_store: Any = None,
    ) -> None:
        self.writer = writer or EventWriter()
        self.tool_repository_root = Path(tool_repository_root or REPO_ROOT).resolve()
        self.artifact_root = (
            Path(artifact_root)
            if artifact_root is not None
            else self.tool_repository_root / "artifacts" / "runs"
        )
        self.env_file = Path(env_file) if env_file is not None else self.tool_repository_root / ".env"
        self.memory_path = (
            Path(memory_path)
            if memory_path is not None
            else self.tool_repository_root / ".tui_memory.json"
        )
        self.settings_path = self.memory_path.with_name(".tui_settings.json")
        self._architect_client = architect_client
        self._architect_client_injected = architect_client is not None
        self._tool_generate_text = tool_generate_text
        self._pending_tool_diff: (
            ApplySearchReplaceResponse | CreateFileResponse | MoveFileResponse | None
        ) = None
        self._pending_tool_diffs: list[
            ApplySearchReplaceResponse | CreateFileResponse | MoveFileResponse
        ] = []
        self._pending_action_approval: PendingActionApproval | None = None
        self._chat_history: list[dict[str, str]] = []
        self._compaction_in_progress = False
        self._session_cost_usd = 0.0
        self._cost_warning_emitted = False
        # A failed architect connection should not repeatedly open planning
        # mode and make the user wait through the same retry budget.
        self._architect_unavailable_reason: str | None = None
        self._assistant_busy = False
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._credential_store = credential_store or default_credential_store()
        self._session_credentials = SessionCredentialStore()
        saved_settings = self._load_settings_payload().get("provider", {})
        try:
            self._provider_settings = ProviderSettings(
                Provider(str(saved_settings["name"])), str(saved_settings["endpoint"]),
                str(saved_settings["model"]), float(saved_settings.get("cost_cap_usd", 1.0)),
                bool(saved_settings.get("local_development_confirmed")),
            )
            self._provider_settings.validate()
            self._provider_settings_explicit = True
        except (KeyError, TypeError, ValueError):
            self._provider_settings = ProviderSettings(
                Provider.DEEPSEEK, DEFAULT_ARCHITECT_API_BASE_URL, DEFAULT_ARCHITECT_MODEL
            )
            self._provider_settings_explicit = False
        self._contribution_split = self._load_contribution_default()
        self._contribution = ContributionTelemetry(self._contribution_split)
        self._schedule = WeightedSchedule(self._contribution_split, f"tui-{os.getpid()}")
        self._cost_guard = ApiCostGuard(self._provider_settings.cost_cap_usd)
        self._api_overage_approved = False
        self._permission_evaluator = PermissionEvaluator()
        self._session_id = f"tui-{uuid.uuid4().hex[:12]}"
        self._checkpoint_store = CheckpointStore(
            self.tool_repository_root, self.artifact_root.parent / "tui_checkpoints"
        )
        self._checkpoint_parent: str | None = None
        self._checkpoint_conversations: dict[str, list[dict[str, str]]] = {}
        self._orchestration_store = OrchestrationStore(self.artifact_root.parent / "orchestrations")
        self._active_orchestration_id: str | None = None

    def emit_startup_status(self) -> None:
        child_env = self._child_environment()
        configured = bool(
            child_env.get("ARCHITECT_API_KEY", "").strip()
            or child_env.get("DEEPSEEK_API_KEY", "").strip()
        )
        source = self._architect_key_source()
        self._emit_config_status(configured=configured, source=source, reachable=None)
        if not configured:
            self.writer.emit(
                "log",
                level="warning",
                msg=(
                    "DeepSeek is not configured. Set DEEPSEEK_API_KEY in the "
                    "repository .env file or export it before sending a prompt."
                ),
            )
            threading.Thread(target=self.research_readiness, daemon=True).start()
            return
        # DNS resolution can block unpredictably on an offline network.  The
        # bridge must start reading commands before that check completes.
        threading.Thread(
            target=self._emit_deepseek_reachability,
            args=(source,),
            daemon=True,
        ).start()
        threading.Thread(target=self.research_readiness, daemon=True).start()

    def _emit_config_status(
        self,
        *,
        configured: bool,
        source: str,
        reachable: bool | None,
    ) -> None:
        self.writer.emit(
            "config_status",
            deepseek_configured=configured,
            deepseek_reachable=reachable,
            source=source,
            memory_path=str(self.memory_path),
            preference_count=len(self._load_preferences()),
            architect_mode=self._architect_mode(),
            local_model=DEFAULT_OLLAMA_MODEL,
        )

    def _emit_deepseek_reachability(self, source: str) -> None:
        reachable = self._deepseek_reachable()
        self._emit_config_status(configured=True, source=source, reachable=reachable)
        if not reachable:
            self.writer.emit(
                "log",
                level="warning",
                msg="DeepSeek host cannot be resolved. Check your internet or DNS before sending a prompt.",
            )

    def handle(self, command: dict[str, Any]) -> None:
        kind = str(command.get("cmd") or "")
        if kind == "run":
            self.start_run(
                str(command.get("entrypoint") or ""),
                [str(item) for item in command.get("args") or []],
            )
        elif kind in {"prompt", "chat"}:
            self.start_chat(str(command.get("text") or ""))
        elif kind == "draft_spec":
            self.start_spec_draft()
        elif kind == "draft_research":
            self.start_research()
        elif kind == "questionnaire_complete":
            self.complete_questionnaire(command.get("answers"))
        elif kind == "execute_spec":
            self.start_spec_execution(str(command.get("text") or ""))
        elif kind == "tool_task":
            self.start_tool_task(
                str(command.get("text") or ""),
                str(command.get("provider") or "auto"),
            )
        elif kind == "apply_tool_diff":
            self.resolve_tool_diff(bool(command.get("approved")))
        elif kind == "approve_action":
            self.resolve_action_approval(bool(command.get("approved")))
        elif kind == "check":
            self.check_code(str(command.get("path") or ""))
        elif kind == "cancel":
            self.cancel()
        elif kind == "repo_map":
            self.repo_map(
                str(command.get("root") or REPO_ROOT),
                str(command.get("focus") or ""),
                str(command.get("mode") or "diagram"),
            )
        elif kind == "compile":
            self.compile_source(
                str(command.get("language") or ""),
                str(command.get("source") or ""),
            )
        elif kind == "profile_samples":
            self.profile_samples(
                str(command.get("loop_order") or ""),
                command.get("samples_ns"),
                command.get("cache_misses"),
            )
        elif kind == "compute_shield":
            self.compute_shield(command.get("phase"), command.get("tasks"))
        elif kind == "history":
            self.history(command.get("run_id"), command.get("limit"))
        elif kind == "research_readiness":
            self.research_readiness()
        elif kind == "repair_session_action":
            self.repair_session_action(command)
        elif kind == "orchestrate":
            self.orchestrate(str(command.get("goal") or ""))
        elif kind == "approve_graph":
            self.approve_graph(str(command.get("session_id") or ""), str(command.get("revision_hash") or ""))
        elif kind == "orchestration_action":
            self.orchestration_action(command)
        elif kind == "inspect_orchestration":
            self.inspect_orchestration(str(command.get("session_id") or ""))
        elif kind == "replay_orchestration":
            self.replay_orchestration(str(command.get("session_id") or ""))
        elif kind == "open_settings":
            self.emit_settings_state()
        elif kind == "save_provider_settings":
            self.save_provider_settings(command)
        elif kind == "test_provider_connection":
            threading.Thread(target=self.test_provider_connection, daemon=True).start()
        elif kind == "clear_provider_credential":
            self.clear_provider_credential(str(command.get("provider") or self._provider_settings.provider.value))
        elif kind == "set_contribution_split":
            self.set_contribution_split(command)
        elif kind == "cost_cap_approval":
            self._api_overage_approved = bool(command.get("approved"))
            self.writer.emit("cost_cap_approval", approved=self._api_overage_approved, remaining_api_budget=self._cost_guard.remaining_usd)
        elif kind == "set_permission_mode":
            self.set_permission_mode(str(command.get("mode") or "default"))
        elif kind == "clear_context":
            self.clear_context()
        elif kind == "compact_context":
            self.compact_context(str(command.get("instructions") or ""))
        elif kind == "context_status":
            self.emit_context_state()
        elif kind == "list_checkpoints":
            self.emit_checkpoint_list()
        elif kind == "rewind":
            self.rewind(str(command.get("checkpoint_id") or ""), str(command.get("scope") or "both"))
        elif kind == "branch_checkpoint":
            self.branch_checkpoint(str(command.get("checkpoint_id") or ""))
        elif kind in {"extensions_status", "mcp_status"}:
            self.emit_extensions_state()
        else:
            self.writer.emit("log", level="error", msg=f"unknown command: {kind}")

    def emit_settings_state(self) -> None:
        credential, _source = self._resolved_provider_credential(self._provider_settings.provider)
        metadata = credential_metadata(credential)
        parsed = urlparse(self._provider_settings.endpoint)
        self.writer.emit(
            "settings_state", provider=self._provider_settings.provider.value,
            endpoint_hostname=parsed.hostname or "", endpoint=self._provider_settings.endpoint,
            model=self._provider_settings.model, cost_cap_usd=self._cost_guard.cap_usd,
            local_development_confirmed=self._provider_settings.local_development_confirmed,
            **metadata,
        )
        self.writer.emit(
            "contribution_state", qwen=self._contribution_split.qwen,
            api=self._contribution_split.api, remaining_api_budget=self._cost_guard.remaining_usd,
            telemetry=self._contribution.snapshot(),
        )

    def save_provider_settings(self, command: dict[str, Any]) -> None:
        try:
            provider = Provider(str(command.get("provider") or "deepseek"))
            settings = ProviderSettings(
                provider, str(command.get("endpoint") or ""), str(command.get("model") or ""),
                float(command.get("cost_cap_usd", 1.0)),
                bool(command.get("local_development_confirmed")),
            )
            settings.validate()
            credential = command.get("credential")
            if credential is not None:
                # Consume the private-channel value immediately; no event includes it.
                value = str(credential)
                if value:
                    self._credential_store.set(provider.value, value)
                del value
            self._provider_settings = settings
            self._provider_settings_explicit = True
            self._cost_guard.cap_usd = settings.cost_cap_usd
            payload = self._load_settings_payload()
            payload["provider"] = {
                "name": settings.provider.value, "endpoint": settings.endpoint,
                "model": settings.model, "cost_cap_usd": settings.cost_cap_usd,
                "local_development_confirmed": settings.local_development_confirmed,
            }
            self._write_settings_payload(payload)
            if not self._architect_client_injected:
                self._architect_client = None
            self.emit_settings_state()
        except (ValueError, RuntimeError) as exc:
            self.writer.emit("provider_connection_result", ok=False, message=str(exc))

    def clear_provider_credential(self, provider: str) -> None:
        try:
            self._credential_store.clear(Provider(provider).value)
            self._session_credentials.clear(provider)
            self.emit_settings_state()
        except (ValueError, RuntimeError) as exc:
            self.writer.emit("provider_connection_result", ok=False, message=str(exc))

    def test_provider_connection(self) -> None:
        try:
            credential, _source = self._resolved_provider_credential(self._provider_settings.provider)
            headers = {"Authorization": f"Bearer {credential}"} if credential else {}
            probe = _provider_probe_url(self._provider_settings)
            response = httpx.get(probe, headers=headers, timeout=5.0, follow_redirects=False)
            if response.status_code in {401, 403}:
                self.writer.emit("provider_connection_result", ok=False, message="credential rejected")
            elif response.status_code >= 500:
                self.writer.emit("provider_connection_result", ok=False, message=f"provider returned HTTP {response.status_code}")
            else:
                self.writer.emit("provider_connection_result", ok=True, message="provider endpoint and credential accepted")
        except (httpx.HTTPError, ValueError):
            self.writer.emit("provider_connection_result", ok=False, message="provider connection failed")

    def set_contribution_split(self, command: dict[str, Any]) -> None:
        try:
            split = ContributionSplit(int(command.get("qwen", 50)), int(command.get("api", 50)))
        except (TypeError, ValueError) as exc:
            self.writer.emit("log", level="error", msg=str(exc))
            return
        self._contribution_split = split
        self._contribution = ContributionTelemetry(split)
        self._schedule = WeightedSchedule(split, f"tui-{os.getpid()}")
        if bool(command.get("save_default")):
            payload = self._load_settings_payload()
            payload["contribution"] = {"qwen": split.qwen, "api": split.api}
            self._write_settings_payload(payload)
        self.emit_settings_state()

    def _load_contribution_default(self) -> ContributionSplit:
        try:
            payload = self._load_settings_payload()
            contribution = payload.get("contribution", {})
            return ContributionSplit(int(contribution["qwen"]), int(contribution["api"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ContributionSplit()

    def _load_settings_payload(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1}
        return payload if isinstance(payload, dict) else {"version": 1}

    def _write_settings_payload(self, payload: dict[str, Any]) -> None:
        payload["version"] = 1
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.settings_path)

    def set_permission_mode(self, mode: str) -> None:
        try:
            selected = PermissionMode(mode)
        except ValueError:
            self.writer.emit("log", level="error", msg=f"unknown permission mode: {mode}")
            return
        self._permission_evaluator.mode = selected
        self.writer.emit("permission_mode_state", mode=selected.value)

    def clear_context(self) -> None:
        self._chat_history.clear()
        self.writer.emit("context_state", summary="Context cleared.", message_count=0, checkpoint_ids=[], cleared=True)

    def compact_context(self, instructions: str = "") -> None:
        if self._compaction_in_progress:
            self.writer.emit("log", level="warning", msg="context compaction is already running")
            return
        self._compaction_in_progress = True
        self.writer.emit("assistant_status", stage="compacting_context", busy=True)
        def compact() -> None:
            try:
                if instructions.strip():
                    self._chat_history.append({"role": "user", "content": f"COMPACTION INSTRUCTIONS: {instructions.strip()}"})
                self._run_compaction()
                self.emit_context_state()
            except Exception as exc:  # noqa: BLE001 - background protocol boundary
                self.writer.emit("chat_error", stage="compacting_context", message=f"{type(exc).__name__}: {exc}")
            finally:
                self._compaction_in_progress = False
                self.writer.emit("assistant_status", stage="compacting_context", busy=False)
        threading.Thread(target=compact, daemon=True).start()

    def emit_context_state(self) -> None:
        checkpoints = [item.checkpoint_id for item in self._checkpoint_store.list(self._session_id)]
        summary = _chat_transcript(self._chat_history[-6:]) or "No active conversation context."
        self.writer.emit(
            "context_state", summary=summary[:4000], message_count=len(self._chat_history),
            checkpoint_ids=checkpoints, permission_mode=self._permission_evaluator.mode.value,
            contribution=self._contribution.snapshot(), remaining_api_budget=self._cost_guard.remaining_usd,
        )

    def _create_edit_checkpoint(self, paths: list[str]) -> None:
        checkpoint = self._checkpoint_store.create(
            self._session_id, paths, parent_id=self._checkpoint_parent,
            conversation_summary=_chat_transcript(self._chat_history[-8:]), approval_state="approved",
        )
        self._checkpoint_parent = checkpoint.checkpoint_id
        self._checkpoint_conversations[checkpoint.checkpoint_id] = [dict(item) for item in self._chat_history]
        self.writer.emit("checkpoint_created", checkpoint_id=checkpoint.checkpoint_id, parent_id=checkpoint.parent_id or "", changed_paths=list(checkpoint.changed_paths))

    def emit_checkpoint_list(self) -> None:
        self.writer.emit("checkpoint_list", checkpoints=[asdict(item) for item in self._checkpoint_store.list(self._session_id)])

    def rewind(self, checkpoint_id: str, scope: str) -> None:
        if scope not in {"code", "conversation", "both"}:
            self.writer.emit("rewind_result", ok=False, checkpoint_id=checkpoint_id, message="invalid rewind scope")
            return
        try:
            target = next(item for item in self._checkpoint_store.list(self._session_id) if item.checkpoint_id == checkpoint_id)
            if scope in {"code", "both"}:
                self._create_edit_checkpoint(list(target.changed_paths))
                self._checkpoint_store.restore(checkpoint_id)
            if scope in {"conversation", "both"}:
                history = self._checkpoint_conversations.get(checkpoint_id)
                self._chat_history = [dict(item) for item in history] if history is not None else [{"role": "assistant", "content": target.conversation_summary}]
            self.writer.emit("rewind_result", ok=True, checkpoint_id=checkpoint_id, message=f"restored {scope}")
        except (OSError, ValueError, StopIteration, json.JSONDecodeError) as exc:
            self.writer.emit("rewind_result", ok=False, checkpoint_id=checkpoint_id, message=f"{type(exc).__name__}: {exc}")

    def branch_checkpoint(self, checkpoint_id: str) -> None:
        try:
            child = self._checkpoint_store.branch(checkpoint_id)
            self.writer.emit("session_branched", parent_session_id=self._session_id, session_id=child, checkpoint_id=checkpoint_id)
        except (ValueError, StopIteration) as exc:
            self.writer.emit("rewind_result", ok=False, checkpoint_id=checkpoint_id, message=str(exc))

    def emit_extensions_state(self) -> None:
        entries = []
        manifest_root = self.tool_repository_root / ".harness" / "extensions"
        for path in sorted(manifest_root.glob("*.json")):
            try:
                manifest = ExtensionManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
                entries.append({"name": manifest.name, "version": manifest.version, "capabilities": sorted(manifest.capabilities), "status": "ready"})
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                entries.append({"name": path.stem, "version": "", "capabilities": [], "status": f"invalid: {exc}"})
        self.writer.emit("extensions_state", extensions=entries)

    def _extension_manifests(self) -> list[ExtensionManifest]:
        manifests = []
        for path in sorted((self.tool_repository_root / ".harness" / "extensions").glob("*.json")):
            try:
                manifests.append(ExtensionManifest.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return manifests

    def _tool_registry(self):
        registry = build_default_tool_registry(repository_root=self.tool_repository_root)
        register_extension_tools(registry, self._extension_manifests(), self._permission_evaluator)
        return registry

    def start_chat(self, text: str, *, approved: bool = False, already_visible: bool = False) -> None:
        text = text.strip()
        if not text:
            self.writer.emit("log", level="warning", msg="chat message cannot be empty")
            return
        if self._assistant_busy:
            self.writer.emit("log", level="warning", msg="DeepSeek is already responding")
            return
        planning_requested = _should_start_planning(text)
        if planning_requested:
            unavailable = self._planning_unavailable_reason()
            if unavailable:
                self._emit_planning_unavailable(unavailable)
                return
        reason = _action_approval_reason(text)
        if reason:
            decision = self._permission_evaluator.evaluate("write", "chat_repository_change")
            if not decision.allowed and not decision.approval_required:
                self.writer.emit("chat_error", stage="permission", message=decision.reason)
                return
        if reason and not approved:
            self._pending_action_approval = PendingActionApproval(text, reason, "chat")
            self.writer.emit("chat_message", role="user", content=text)
            self.writer.emit("action_approval", request=text, reason=reason)
            return
        uses_api = planning_requested or not _is_local_eligible_task(text)
        if uses_api and not approved and not self._architect_client_injected:
            decision = self._permission_evaluator.evaluate("network", "provider_api_call")
            if not decision.allowed:
                if decision.approval_required:
                    self._pending_action_approval = PendingActionApproval(
                        text, "The configured API provider requires a network request.", "chat"
                    )
                    self.writer.emit("chat_message", role="user", content=text)
                    self.writer.emit("action_approval", request=text, reason="The configured API provider requires a network request.")
                else:
                    self.writer.emit("chat_error", stage="permission", message=decision.reason)
                return
        self._chat_history.append({"role": "user", "content": text})
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
        if not already_visible:
            self.writer.emit("chat_message", role="user", content=text)
        remembered = self._preference_from_message(text)
        if remembered:
            added = self._remember_preference(remembered)
            self.writer.emit(
                "memory_updated",
                preference=remembered,
                added=added,
                count=len(self._load_preferences()),
            )
        self._start_assistant_task("chat", self._run_chat)

    def start_spec_draft(self) -> None:
        if self._assistant_busy:
            self.writer.emit("log", level="warning", msg="DeepSeek is already responding")
            return
        if not self._chat_history:
            self.writer.emit(
                "log",
                level="warning",
                msg="Discuss the task in chat before drafting a specification.",
            )
            return
        self._start_assistant_task("drafting_spec", self._run_spec_draft)

    def start_research(self) -> None:
        if self._assistant_busy:
            self.writer.emit("log", level="warning", msg="an assistant task is already running")
            return
        if not any(message.get("role") == "user" for message in self._chat_history):
            self.writer.emit("log", level="warning", msg="ask a question in chat before researching it")
            return
        self._start_assistant_task("research", self._run_research)

    def complete_questionnaire(self, answers: Any) -> None:
        if self._assistant_busy:
            self.writer.emit("log", level="warning", msg="DeepSeek is already responding")
            return
        normalized: list[dict[str, str]] = []
        if isinstance(answers, list):
            for answer in answers[:MAX_QUESTIONS]:
                if not isinstance(answer, dict):
                    continue
                question_text = str(answer.get("question_text") or "").strip()
                value = str(answer.get("answer") or "").strip()
                if question_text and value:
                    normalized.append(
                        {"question_text": question_text, "answer": value}
                    )
        if not normalized:
            self.writer.emit(
                "log",
                level="warning",
                msg="questionnaire completion requires at least one answer",
            )
            return
        transcript = "QUESTIONNAIRE ANSWERS:\n" + "\n".join(
            f"- {item['question_text']}: {item['answer']}" for item in normalized
        )
        self._chat_history.append({"role": "user", "content": transcript})
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
        self.writer.emit("chat_message", role="user", content=transcript)
        self._start_assistant_task("drafting_spec", self._run_spec_draft)

    def start_spec_execution(self, text: str) -> None:
        if not text.strip():
            self.writer.emit("log", level="warning", msg="approved spec cannot be empty")
            return
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="harness-tui-prompt-",
            suffix=".md",
            delete=False,
        ) as stream:
            stream.write(text)
            spec_path = Path(stream.name)
        self.start_run(
            "structured_spec",
            ["--spec", str(spec_path), "--save-artifacts"],
            cleanup_path=spec_path,
        )

    def start_tool_task(
        self,
        text: str,
        provider: str = "deepseek",
        *,
        approved: bool = False,
        already_visible: bool = False,
    ) -> None:
        task = text.strip()
        if not task:
            self.writer.emit("log", level="warning", msg="tool task cannot be empty")
            return
        if self._assistant_busy:
            self.writer.emit("log", level="warning", msg="an assistant task is already running")
            return
        reason = _action_approval_reason(task)
        capability = "write" if reason else "read"
        decision = self._permission_evaluator.evaluate(capability, "repository_tool_task")
        if not decision.allowed and not decision.approval_required:
            self.writer.emit("chat_error", stage="permission", message=decision.reason)
            return
        if reason and not approved:
            self._pending_action_approval = PendingActionApproval(
                task,
                reason,
                "tool_task",
                provider,
            )
            self.writer.emit("chat_message", role="user", content=task)
            self.writer.emit("action_approval", request=task, reason=reason)
            return
        if provider.strip().lower() in {"deepseek", "auto"} and not approved and not self._architect_client_injected and self._tool_generate_text is None:
            decision = self._permission_evaluator.evaluate("network", "provider_api_call")
            if not decision.allowed:
                if decision.approval_required:
                    self._pending_action_approval = PendingActionApproval(
                        task, "The configured API provider requires a network request.", "tool_task", provider
                    )
                    self.writer.emit("chat_message", role="user", content=task)
                    self.writer.emit("action_approval", request=task, reason="The configured API provider requires a network request.")
                else:
                    self.writer.emit("chat_error", stage="permission", message=decision.reason)
                return
        if _should_start_planning(task):
            self.writer.emit(
                "log",
                level="info",
                msg="planning request routed to spec intake",
            )
            self.start_chat(task, approved=approved, already_visible=already_visible)
            return
        selected_provider = provider.strip().lower()
        if selected_provider not in {"auto", "qwen", "deepseek"}:
            self.writer.emit(
                "log",
                level="error",
                msg=f"unsupported tool provider: {provider}",
            )
            return
        self._pending_tool_diff = None
        self._pending_tool_diffs = []
        self._chat_history.append({"role": "user", "content": task})
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
        if not already_visible:
            self.writer.emit("chat_message", role="user", content=task)
        self._start_assistant_task(
            "repository_tools",
            lambda: self._run_tool_task(
                task,
                selected_provider,
                allowed_tools=(
                    "search_directory",
                    "read_file",
                    "apply_search_replace",
                    "create_file",
                    "move_file",
                    "execute_script",
                ),
            ),
        )

    def _run_tool_task(
        self,
        task: str,
        provider: str,
        *,
        allowed_tools: tuple[str, ...] | None = None,
    ) -> None:
        proposals: list[ApplySearchReplaceResponse | CreateFileResponse | MoveFileResponse] = []
        if allowed_tools is None:
            if provider == "qwen":
                allowed_tools = (
                    ("search_directory", "read_file", "create_file", "move_file")
                    if _is_filesystem_mutation_request(task)
                    else ("search_directory", "read_file")
                )
            else:
                allowed_tools = (
                    "search_directory",
                    "read_file",
                    "apply_search_replace",
                    "create_file",
                    "move_file",
                    "execute_script",
                )

        def on_tool_result(record: ToolCallRecord, raw_value: Any) -> None:
            ok = bool(record.result.get("ok"))
            error = str(record.result.get("error") or "")
            self.writer.emit(
                "tool_call",
                turn=record.turn,
                tool=record.tool,
                ok=ok,
                summary="completed" if ok else error,
            )
            if isinstance(raw_value, ReadFileResponse):
                excerpt, start_line, truncated = _code_excerpt(raw_value.content)
                self.writer.emit(
                    "code_excerpt",
                    path=raw_value.path,
                    start_line=start_line,
                    content=excerpt,
                    truncated=raw_value.truncated or truncated,
                )
            if isinstance(
                raw_value,
                (ApplySearchReplaceResponse, CreateFileResponse, MoveFileResponse),
            ):
                proposals.append(raw_value)

        try:
            run = ToolCallingAgent(
                self._tool_generator(provider),
                self._tool_registry(),
                max_turns=8,
                on_tool_result=on_tool_result,
                allowed_tools=allowed_tools,
                max_mutation_proposals=4,
            ).run(task)
        except RuntimeError as exc:
            if provider == "deepseek":
                self._report_deepseek_unavailable(exc)
                return
            raise
        answer = run.final_answer or "I could not produce a final answer."
        failed_mutations = [
            call
            for call in run.calls
            if call.tool in {"apply_search_replace", "create_file", "move_file"}
            and not call.result.get("ok")
        ]
        if not proposals and failed_mutations:
            answer = (
                "No reviewed change was prepared. "
                + str(failed_mutations[-1].result.get("error") or "The tool proposal failed.")
            )
        self._chat_history.append({"role": "assistant", "content": answer})
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
        self.writer.emit(
            "tool_answer",
            answer=answer,
            exhausted=run.exhausted,
            call_count=len(run.calls),
        )
        self._queue_tool_diffs(proposals)

    def _tool_generator(self, provider: str):
        if self._tool_generate_text is not None:
            return self._tool_generate_text
        if provider == "auto":
            return lambda prompt: self._generate_eligible(
                prompt,
                system=(
                    "Return one repository tool-call JSON object only. "
                    f"Host operating system: {_host_operating_system()}."
                ),
            )
        if provider == "deepseek":
            return lambda prompt: self._generate_architect(
                prompt,
                system=(
                    "Return one repository tool-call JSON object only. "
                    f"Host operating system: {_host_operating_system()}."
                ),
            )
        client = OllamaClient(keep_alive=self._ollama_keep_alive())
        return lambda prompt: self._generate_local(
            client,
            prompt,
            system=(
                "Return one repository tool-call JSON object only. "
                f"Host operating system: {_host_operating_system()}."
            ),
        )

    def _generate_eligible(self, prompt: str, *, system: str) -> str:
        scheduled = self._schedule.provider_at(len(self._contribution.scheduled))
        if scheduled == "qwen":
            response = self._generate_local(
                OllamaClient(keep_alive=self._ollama_keep_alive()), prompt, system=system
            )
        else:
            response = self._generate_architect(prompt, system=system)
        self._contribution.record(scheduled, scheduled)
        self.writer.emit(
            "contribution_state",
            qwen=self._contribution_split.qwen,
            api=self._contribution_split.api,
            remaining_api_budget=self._cost_guard.remaining_usd,
            telemetry=self._contribution.snapshot(),
        )
        return response

    def _generate_local(self, client: OllamaClient, prompt: str, *, system: str) -> str:
        context_window = self._context_window("local")
        response = client.generate(
            prompt,
            model=DEFAULT_OLLAMA_MODEL,
            config=OllamaGenerationConfig(
                temperature=0.0,
                num_predict=1200,
                num_ctx=context_window,
            ),
            system=system,
        )
        usage = client.last_usage
        self._emit_context_usage(
            backend="local",
            model=DEFAULT_OLLAMA_MODEL,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            context_window=context_window,
            estimated_cost_usd=0.0,
        )
        return response

    def _generate_architect(
        self,
        prompt: str,
        *,
        system: str,
        history_carrying: bool = False,
    ) -> str:
        estimated_cost = 0.01
        if self._cost_guard.decision(estimated_cost, self._api_overage_approved) == "approval_required":
            self.writer.emit(
                "cost_cap_approval",
                approved=False,
                remaining_api_budget=self._cost_guard.remaining_usd,
            )
            raise RuntimeError("API cost cap reached; explicit overage approval is required")
        self._api_overage_approved = False
        client = self._client()
        response = client.generate(prompt, system=system)
        usage = getattr(client, "last_usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0)
        completion_tokens = getattr(usage, "completion_tokens", 0)
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            self._cost_guard.record(float(getattr(usage, "estimated_cost_usd", 0.0) or 0.0))
            self._emit_context_usage(
                backend="api",
                model=str(getattr(usage, "model", DEFAULT_ARCHITECT_MODEL)),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                context_window=self._context_window("api"),
                estimated_cost_usd=float(getattr(usage, "estimated_cost_usd", 0.0) or 0.0),
                history_carrying=history_carrying,
            )
        return response

    def _emit_context_usage(
        self,
        *,
        backend: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        context_window: int,
        estimated_cost_usd: float,
        history_carrying: bool = False,
    ) -> None:
        total_tokens = max(0, int(prompt_tokens)) + max(0, int(completion_tokens))
        context_window = max(1, int(context_window))
        self.writer.emit(
            "context_usage",
            backend=backend,
            model=model,
            prompt_tokens=max(0, int(prompt_tokens)),
            completion_tokens=max(0, int(completion_tokens)),
            total_tokens=total_tokens,
            context_window=context_window,
            estimated_cost_usd=max(0.0, float(estimated_cost_usd)),
        )
        if backend == "api":
            self._session_cost_usd += max(0.0, float(estimated_cost_usd))
            if (
                self._session_cost_usd >= COST_WARNING_THRESHOLD_USD
                and not self._cost_warning_emitted
            ):
                self._cost_warning_emitted = True
                self.writer.emit(
                    "log",
                    level="warning",
                    msg=f"session cost has reached ${self._session_cost_usd:.2f}",
                )
        if history_carrying:
            self._maybe_compact(total_tokens / context_window)

    def _maybe_compact(self, utilization: float) -> None:
        if utilization < COMPACTION_TRIGGER_RATIO:
            return
        if len(self._chat_history) < 4 or self._compaction_in_progress:
            return
        self._compaction_in_progress = True
        try:
            self._run_compaction()
        finally:
            self._compaction_in_progress = False

    def _run_compaction(self) -> None:
        # Compaction deliberately uses the small local model. It is a bounded,
        # non-reasoning summary task and must not spend a DeepSeek request just
        # because an API-backed conversation has become long.
        response = self._generate_local(
            OllamaClient(keep_alive=self._ollama_keep_alive()),
            "\n\n".join([
                "CONVERSATION:\n" + _chat_transcript(self._chat_history),
                "ACTIVE DIFFS:\n" + "\n".join(getattr(item, "path", "") for item in self._pending_tool_diffs),
                "CONTRIBUTION:\n" + json.dumps(self._contribution.snapshot(), sort_keys=True),
                f"COST: spent=${self._cost_guard.spent_usd:.6f} remaining=${self._cost_guard.remaining_usd:.6f}",
                "CHECKPOINTS:\n" + "\n".join(item.checkpoint_id for item in self._checkpoint_store.list(self._session_id)),
            ]),
            system=COMPACTION_SYSTEM_PROMPT,
        ).strip()
        try:
            summary = _render_compaction_summary(response)
        except ValueError:
            return
        self._chat_history = [{
            "role": "assistant",
            "content": "[context compacted; full history summarized below]\n\n" + summary,
        }]
        self.writer.emit("log", level="info", msg="context compacted")

    def resolve_tool_diff(self, approved: bool) -> None:
        proposal = self._pending_tool_diff
        if proposal is None:
            self.writer.emit("log", level="warning", msg="no tool diff is pending review")
            return
        decision = self._permission_evaluator.evaluate("write", "apply_reviewed_diff")
        if approved and not decision.allowed and not decision.approval_required:
            self.writer.emit("tool_diff_resolved", path=proposal.path, applied=False, message=decision.reason)
            return
        try:
            if approved:
                paths = [proposal.path]
                if isinstance(proposal, MoveFileResponse):
                    paths.append(proposal.destination)
                self._create_edit_checkpoint(paths)
            if isinstance(proposal, CreateFileResponse):
                result = apply_reviewed_create_file(
                    self.tool_repository_root,
                    proposal,
                    approved=approved,
                )
            elif isinstance(proposal, MoveFileResponse):
                result = apply_reviewed_move_file(
                    self.tool_repository_root,
                    proposal,
                    approved=approved,
                )
            else:
                result = apply_reviewed_search_replace(
                    self.tool_repository_root,
                    proposal,
                    approved=approved,
                )
        except Exception as exc:  # noqa: BLE001 - protocol boundary
            self.writer.emit(
                "tool_diff_resolved",
                path=proposal.path,
                applied=False,
                message=f"{type(exc).__name__}: {exc}",
            )
            return
        if self._pending_tool_diffs and self._pending_tool_diffs[0] is proposal:
            self._pending_tool_diffs.pop(0)
        self._pending_tool_diff = None
        self.writer.emit(
            "tool_diff_resolved",
            path=result.path,
            applied=result.applied,
            message="diff applied" if result.applied else "diff discarded",
        )
        self._present_next_tool_diff()

    def _queue_tool_diffs(
        self,
        proposals: list[ApplySearchReplaceResponse | CreateFileResponse | MoveFileResponse],
    ) -> None:
        if not proposals:
            return
        self._pending_tool_diff = None
        self._pending_tool_diffs = list(proposals)
        self._present_next_tool_diff()

    def _present_next_tool_diff(self) -> None:
        if not self._pending_tool_diffs:
            return
        proposal = self._pending_tool_diffs[0]
        self._pending_tool_diff = proposal
        remaining = len(self._pending_tool_diffs)
        self.writer.emit(
            "tool_diff",
            path=(
                f"{proposal.path} → {proposal.destination}"
                if isinstance(proposal, MoveFileResponse)
                else proposal.path
            ),
            diff=proposal.diff,
            replacements=getattr(proposal, "replacements", 1),
            pending_count=remaining,
        )
        if self._permission_evaluator.mode is PermissionMode.ACCEPT_EDITS:
            self.resolve_tool_diff(True)

    def resolve_action_approval(self, approved: bool) -> None:
        pending = self._pending_action_approval
        if pending is None:
            self.writer.emit("log", level="warning", msg="no action is pending approval")
            return
        self._pending_action_approval = None
        if not approved:
            self.writer.emit(
                "log",
                level="info",
                msg="requested repository action declined; no tool or file operation was started",
            )
            return
        self.writer.emit(
            "log",
            level="info",
            msg="repository action approved; preparing the requested work",
        )
        if pending.route == "tool_task":
            self.start_tool_task(
                pending.text,
                pending.provider,
                approved=True,
                already_visible=True,
            )
        else:
            self.start_chat(pending.text, approved=True, already_visible=True)

    def check_code(self, path: str) -> None:
        if self._engines_disabled("/check"):
            return
        requested = path.strip()
        if not requested:
            self.writer.emit("log", level="warning", msg="/check requires a repository file path")
            return
        result = self._tool_registry().dispatch(
            "check_code",
            CheckCodeRequest(root=Path("."), path=requested),
        )
        if not result.ok or not isinstance(result.value, CheckCodeResponse):
            self.writer.emit(
                "log",
                level="error",
                msg=result.error or "check_code did not return a result",
            )
            return
        self.writer.emit(
            "check_result",
            path=result.value.path,
            passed=result.value.passed,
            findings=result.value.findings,
        )

    def _start_assistant_task(self, stage: str, target: Any) -> None:
        self._assistant_busy = True
        self.writer.emit("assistant_status", stage=stage, busy=True)
        threading.Thread(
            target=self._finish_assistant_task,
            args=(stage, target),
            daemon=True,
        ).start()

    def _finish_assistant_task(self, stage: str, target: Any) -> None:
        try:
            target()
        except Exception as exc:  # noqa: BLE001 - assistant protocol boundary
            self.writer.emit(
                "chat_error",
                stage=stage,
                message=f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._assistant_busy = False
            self.writer.emit("assistant_status", stage=stage, busy=False)

    def _run_chat(self) -> None:
        latest = self._chat_history[-1]["content"] if self._chat_history else ""
        planning_requested = _should_start_planning(latest)
        if planning_requested:
            try:
                response = self._generate_planner(
                    self._chat_prompt(), QUESTIONNAIRE_SYSTEM_PROMPT, history_carrying=True
                ).strip()
            except RuntimeError as exc:
                self._report_deepseek_unavailable(exc, label="Planning")
                return
            message, questions = _parse_questionnaire_response(response)
            history_content = (
                _questionnaire_transcript(message, questions) if questions else message
            )
            self._chat_history.append({"role": "assistant", "content": history_content})
            self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
            self.writer.emit("chat_message", role="assistant", content=message)
            if questions:
                self.writer.emit("questionnaire", questions=questions)
            return

        if _is_local_eligible_task(latest):
            self._run_tool_task(latest, "qwen")
            return
        if _is_repository_maintenance_request(latest):
            self._run_tool_task(latest, "deepseek")
            return
        if _is_code_generation_request(latest):
            self._run_code_draft(latest)
            return
        self._run_plain_chat()

    def _run_plain_chat(self) -> None:
        try:
            message = self._generate_architect(
                self._chat_prompt(), system=CHAT_SYSTEM_PROMPT, history_carrying=True
            ).strip()
        except RuntimeError as exc:
            self._report_deepseek_unavailable(exc)
            return
        self._chat_history.append({"role": "assistant", "content": message})
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
        self.writer.emit("chat_message", role="assistant", content=message)

    def _run_code_draft(self, task: str) -> None:
        proposals: list[
            ApplySearchReplaceResponse | CreateFileResponse | MoveFileResponse
        ] = []

        def on_tool_result(record: ToolCallRecord, raw_value: Any) -> None:
            ok = bool(record.result.get("ok"))
            self.writer.emit(
                "tool_call",
                turn=record.turn,
                tool=record.tool,
                ok=ok,
                summary="draft prepared" if ok else str(record.result.get("error") or ""),
            )
            if isinstance(
                raw_value,
                (ApplySearchReplaceResponse, CreateFileResponse, MoveFileResponse),
            ):
                proposals.append(raw_value)

        try:
            run = ToolCallingAgent(
                lambda prompt: self._generate_architect(prompt, system=CODE_DRAFT_SYSTEM_PROMPT),
                self._tool_registry(),
                max_turns=8,
                on_tool_result=on_tool_result,
                allowed_tools=(
                    "search_directory",
                    "read_file",
                    "apply_search_replace",
                    "create_file",
                    "move_file",
                ),
                max_mutation_proposals=4,
            ).run(task)
        except RuntimeError as exc:
            self._report_deepseek_unavailable(exc)
            return

        answer = run.final_answer or "I could not prepare a code draft."
        self._chat_history.append({"role": "assistant", "content": answer})
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
        self.writer.emit("tool_answer", answer=answer, exhausted=run.exhausted, call_count=len(run.calls))
        self._queue_tool_diffs(proposals)

    def _run_spec_draft(self) -> None:
        response = self._generate_planner(
            self._spec_prompt(), SPEC_SHEET_SYSTEM_PROMPT, history_carrying=True
        ).strip()
        self.writer.emit("spec_draft", text=_render_spec_sheet(response))

    def _run_research(self) -> None:
        question = next(
            (
                message["content"]
                for message in reversed(self._chat_history)
                if message.get("role") == "user"
            ),
            "",
        )
        findings: list[str] = []

        def on_tool_result(record: ToolCallRecord, raw_value: Any) -> None:
            ok = bool(record.result.get("ok"))
            self.writer.emit(
                "tool_call",
                turn=record.turn,
                tool=record.tool,
                ok=ok,
                summary="completed" if ok else str(record.result.get("error") or ""),
            )
            if isinstance(raw_value, ReadFileResponse):
                excerpt, start_line, _truncated = _code_excerpt(raw_value.content)
                findings.append(f"read {raw_value.path} (line {start_line}):\n{excerpt}")
            elif ok:
                findings.append(f"{record.tool}({record.arguments}) -> {record.result}")

        ToolCallingAgent(
            self._tool_generator("deepseek"),
            self._tool_registry(),
            max_turns=8,
            on_tool_result=on_tool_result,
            allowed_tools=("search_directory", "read_file"),
        ).run(f"Research only; do not modify files: {question}")
        response = self._generate_architect(
            "\n\n".join(
                [
                    f"QUESTION:\n{question}",
                    "FINDINGS:\n" + ("\n\n".join(findings) or "(none)"),
                ]
            ),
            system=RESEARCH_SYSTEM_PROMPT,
        ).strip()
        document = _render_research_doc(question, response)
        path = _save_research_doc(self.tool_repository_root / "docs" / "research", question, document)
        relative_path = path.relative_to(self.tool_repository_root).as_posix()
        self._chat_history.append(
            {"role": "assistant", "content": f"Research saved to {relative_path}"}
        )
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
        self.writer.emit("research_draft", text=document, path=relative_path)

    def _client(self) -> ArchitectApiClient:
        if self._architect_client is None:
            credential, _source = self._resolved_provider_credential(self._provider_settings.provider)
            self._architect_client = ArchitectApiClient(
                ArchitectConfig(
                    env_file=str(self.env_file),
                    api_key_override=credential,
                    base_url_override=self._provider_settings.endpoint,
                    model_override=self._provider_settings.model,
                )
            )
        return self._architect_client

    def _planning_unavailable_reason(self) -> str | None:
        if self._architect_unavailable_reason:
            return self._architect_unavailable_reason
        # Dependency-injected clients are used by callers that already own the
        # architect connection (and by the offline TUI protocol tests).
        if self._architect_client is not None:
            return None
        if self._architect_key_source() == "unconfigured":
            return "Planning needs a DeepSeek API key in .env before it can start."
        return None

    def _deepseek_reachable(self) -> bool:
        host = urlparse(ArchitectConfig(env_file=str(self.env_file)).base_url).hostname
        if not host:
            return False
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            return False
        return True

    def _report_deepseek_unavailable(self, error: RuntimeError, *, label: str = "DeepSeek") -> None:
        detail = str(error)
        if "not reachable" in detail or "timed out" in detail:
            self._architect_unavailable_reason = (
                "DeepSeek could not be reached. Check your internet or DNS, then retry."
            )
        else:
            self._architect_unavailable_reason = "DeepSeek could not complete this request. Check its API configuration, then retry."
        message = f"{label} is unavailable: {self._architect_unavailable_reason}"
        self._chat_history.append({"role": "assistant", "content": message})
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
        self.writer.emit("chat_message", role="assistant", content=message)
        self.writer.emit("log", level="warning", msg=self._architect_unavailable_reason)

    def _emit_planning_unavailable(self, reason: str) -> None:
        message = f"Planning is unavailable: {reason}"
        self._chat_history.append({"role": "assistant", "content": message})
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
        self.writer.emit("chat_message", role="assistant", content=message)
        self.writer.emit("log", level="warning", msg=reason)

    def _engines_disabled(self, action: str) -> bool:
        enabled = self._child_environment().get("HARNESS_ENGINES_ENABLED", "0")
        if enabled.strip().casefold() in {"1", "true", "yes", "on"}:
            return False
        self.writer.emit(
            "log",
            level="info",
            msg=f"{action} is disabled while HARNESS_ENGINES_ENABLED=0",
        )
        return True

    def _generate_planner(
        self, prompt: str, system: str, *, history_carrying: bool = False
    ) -> str:
        """Use DeepSeek for every interactive plan and tool decision."""

        return self._generate_architect(prompt, system=system, history_carrying=history_carrying)

    @staticmethod
    def _architect_mode() -> str:
        return "api"

    def _chat_prompt(self) -> str:
        return "\n\n".join(
            [
                f"HOST OPERATING SYSTEM: {_host_operating_system()}",
                _preference_context(self._load_preferences()),
                "CONVERSATION:\n" + _chat_transcript(self._chat_history),
                "Respond to the latest user message.",
            ]
        )

    def _spec_prompt(self) -> str:
        return "\n\n".join(
            [
                _preference_context(self._load_preferences()),
                "CONVERSATION:\n" + _chat_transcript(self._chat_history),
                "Fill every field in the execution spec sheet now. Convert the user's "
                "choices into explicit files, components, dependencies, rules, examples, "
                "and validation checks. Return the completed JSON object only.",
            ]
        )

    def _architect_key_source(self) -> str:
        _credential, source = self._resolved_provider_credential(self._provider_settings.provider)
        if source == "environment":
            return "environment:DEEPSEEK_API_KEY"
        if source == "dotenv":
            return ".env:DEEPSEEK_API_KEY"
        return source

    def _ollama_keep_alive(self) -> str:
        """Read the selected repository's setting, without overriding shell config."""

        return self._child_environment().get("OLLAMA_KEEP_ALIVE", OLLAMA_KEEP_ALIVE)

    def _context_window(self, backend: str) -> int:
        """Return the configured context window without accepting invalid values."""

        key, fallback = (
            ("LOCAL_CONTEXT_WINDOW", LOCAL_CONTEXT_WINDOW)
            if backend == "local"
            else ("ARCHITECT_CONTEXT_WINDOW", ARCHITECT_CONTEXT_WINDOW)
        )
        try:
            configured = int(self._child_environment().get(key, str(fallback)))
        except (TypeError, ValueError):
            return fallback
        return configured if configured > 0 else fallback

    def _load_preferences(self) -> list[str]:
        try:
            payload = json.loads(self.memory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        preferences = payload.get("preferences", []) if isinstance(payload, dict) else []
        return [str(item) for item in preferences if str(item).strip()][-MAX_PREFERENCES:]

    def _remember_preference(self, preference: str) -> bool:
        preferences = self._load_preferences()
        if preference in preferences:
            return False
        preferences.append(preference)
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory_path.write_text(
            json.dumps(
                {"version": 1, "preferences": preferences[-MAX_PREFERENCES:]},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return True

    @staticmethod
    def _preference_from_message(text: str) -> str:
        lowered = text.casefold()
        prefixes = ("/remember ", "remember that ", "i prefer ")
        for prefix in prefixes:
            if lowered.startswith(prefix):
                preference = text[len(prefix):].strip()
                sensitive = ("api key", "password", "secret", "token", "credential")
                if preference and len(preference) <= 300 and not any(
                    marker in preference.casefold() for marker in sensitive
                ):
                    return preference
        return ""

    def start_run(
        self,
        entrypoint: str,
        args: list[str],
        *,
        cleanup_path: Path | None = None,
    ) -> None:
        script = ENTRYPOINTS.get(entrypoint)
        if script is None:
            self.writer.emit(
                "log",
                level="error",
                msg=f"unknown entrypoint: {entrypoint}",
            )
            return
        try:
            safe_args = _validated_args(args)
        except ValueError as exc:
            self.writer.emit("log", level="error", msg=str(exc))
            return
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                if cleanup_path is not None:
                    cleanup_path.unlink(missing_ok=True)
                self.writer.emit(
                    "log",
                    level="warning",
                    msg="a harness run is already active",
                )
                return
            read_fd: int | None = None
            write_fd: int | None = None
            child_env = self._child_environment()
            popen_options: dict[str, Any] = {}
            if os.name == "posix":
                read_fd, write_fd = os.pipe()
                child_env[EVENT_FD_ENV] = str(write_fd)
                popen_options["pass_fds"] = (write_fd,)
            self._process = subprocess.Popen(
                [sys.executable, str(script), *safe_args],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=child_env,
                **popen_options,
            )
            if write_fd is not None:
                os.close(write_fd)
            process = self._process
        event_thread: threading.Thread | None = None
        if read_fd is not None:
            event_thread = threading.Thread(
                target=self._forward_events,
                args=(read_fd,),
                daemon=True,
            )
            event_thread.start()
        self.writer.emit("engine_progress", engine="harness", pct=1)
        threading.Thread(
            target=self._forward_run,
            args=(process, event_thread, cleanup_path),
            daemon=True,
        ).start()

    def _child_environment(self) -> dict[str, str]:
        child_env = os.environ.copy()
        for key, value in _dotenv_values(self.env_file).items():
            if not child_env.get(key, "").strip():
                child_env[key] = value
        credential, source = self._resolved_provider_credential(self._provider_settings.provider)
        if credential and source not in {"environment", "dotenv"}:
            key = "OPENAI_API_KEY" if self._provider_settings.provider is Provider.OPENAI_COMPATIBLE else "DEEPSEEK_API_KEY"
            child_env[key] = credential
            child_env["ARCHITECT_API_KEY"] = credential
        if self._provider_settings_explicit and self._provider_settings.provider is not Provider.QWEN:
            child_env["ARCHITECT_API_BASE_URL"] = self._provider_settings.endpoint
            child_env["ARCHITECT_MODEL"] = self._provider_settings.model
        return child_env

    def _resolved_provider_credential(self, provider: Provider) -> tuple[str | None, str]:
        return resolve_credential(
            provider,
            environment=os.environ,
            store=self._credential_store,
            dotenv=_dotenv_values(self.env_file),
            session=self._session_credentials,
        )

    def cancel(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            self.writer.emit("log", level="warning", msg="cancellation requested")

    def repo_map(self, root: str, focus: str, mode: str = "diagram") -> None:
        try:
            root_path = resolve_within_root(self.tool_repository_root, root)
            graph = RepoMapAgent().map_repo(root_path)
        except Exception as exc:  # noqa: BLE001 - protocol boundary
            self.writer.emit(
                "log",
                level="error",
                msg=f"repository map failed: {type(exc).__name__}: {exc}",
            )
            return
        if mode == "variables":
            entries = []
            for record in graph.files:
                imports = sorted({item.module for item in record.imports if item.module})
                variables = sorted({item.name for item in record.variables})
                entries.append(
                    {
                        "path": record.path,
                        "imports": imports,
                        "variables": variables,
                    }
                )
            self.writer.emit(
                "repo_map_variables",
                entries=entries,
            )
            return
        if mode == "files":
            entries = []
            for record in graph.files:
                symbols = [*record.classes, *(item.name for item in record.functions)]
                summary = ""
                try:
                    source = (root_path / record.path).read_text(encoding="utf-8")
                    summary = (ast.get_docstring(ast.parse(source)) or "").splitlines()[0]
                except (OSError, UnicodeDecodeError, SyntaxError, IndexError):
                    summary = ""
                if not summary:
                    summary = f"Defines {', '.join(symbols[:8])}." if symbols else "No module-level classes or functions."
                entries.append(
                    {
                        "path": record.path,
                        "summary": summary,
                        "symbols": symbols,
                    }
                )
            self.writer.emit("repo_map_files", entries=entries)
            return
        self.writer.emit(
            "repo_map",
            summary=render_repo_architecture(graph, focus=focus),
            nodes=[asdict(node) for node in graph.nodes],
            edges=[asdict(edge) for edge in graph.edges],
        )

    def compile_source(self, language: str, source: str) -> None:
        if self._engines_disabled("compile"):
            return
        try:
            findings = CompilationEngine(language).scan(source)
        except ValueError as exc:
            self.writer.emit("log", level="error", msg=str(exc))
            return
        failures = [
            finding
            for finding in findings
            if finding.metrics.get("compile_status") in {"fail", "timeout"}
        ]
        skipped = any(
            finding.metrics.get("compile_status") == "skipped"
            for finding in findings
        )
        status = "fail" if failures else ("skipped" if skipped else "pass")
        self.writer.emit(
            "compile_gate_result",
            status=status,
            errors=[finding.details for finding in failures],
        )

    def profile_samples(
        self,
        loop_order: str,
        raw_samples: Any,
        cache_misses: Any,
    ) -> None:
        if self._engines_disabled("profiling"):
            return
        try:
            samples = tuple(int(sample) for sample in raw_samples)
            if len(samples) < 3 or any(sample < 0 for sample in samples):
                raise ValueError
            ordered = sorted(samples)
            middle = len(ordered) // 2
            runtime_ns = (
                ordered[middle]
                if len(ordered) % 2
                else (ordered[middle - 1] + ordered[middle]) // 2
            )
            result = ProfileResult(
                loop_order=loop_order,
                runtime_ns=runtime_ns,
                spread_ns=max(samples) - min(samples),
                cache_misses=(
                    None if cache_misses is None else int(cache_misses)
                ),
                samples_ns=samples,
            )
        except (TypeError, ValueError):
            self.writer.emit(
                "log",
                level="error",
                msg="profile_samples requires at least three non-negative integer samples",
            )
            return
        self.writer.emit(
            "profiling_result",
            loop_order=result.loop_order,
            runtime_ns=result.runtime_ns,
            spread_ns=result.spread_ns,
            cache_misses=result.cache_misses,
        )

    def compute_shield(self, raw_phase: Any, raw_tasks: Any) -> None:
        if self._engines_disabled("compute shield"):
            return
        try:
            tasks = [
                ShieldTaskTokens(
                    task=str(row["task"]),
                    baseline_tokens=int(row["baseline_tokens"]),
                    shielded_tokens=int(row["shielded_tokens"]),
                )
                for row in raw_tasks
            ]
            metrics = compute_shield_metrics(tasks, phase=int(raw_phase or 3))
        except (KeyError, TypeError, ValueError) as exc:
            self.writer.emit(
                "log",
                level="error",
                msg=f"invalid compute-shield metrics: {exc}",
            )
            return
        self.writer.emit(
            "compute_shield_metrics",
            phase=metrics.phase,
            tokens_baseline=metrics.tokens_baseline,
            tokens_shielded=metrics.tokens_shielded,
            delta=metrics.delta,
        )

    def history(self, run_id: Any = None, limit: Any = None) -> None:
        manager = ArtifactManager(self.artifact_root)
        if run_id:
            checkpoint = manager.load_checkpoint(str(run_id))
            if checkpoint is None:
                self.writer.emit(
                    "log",
                    level="warning",
                    msg=f"unknown run: {run_id}",
                )
                return
            self.writer.emit(
                "history_detail",
                run_id=str(run_id),
                checkpoint=checkpoint,
            )
            return
        bound = 20
        if limit is not None:
            try:
                bound = max(0, int(limit))
            except (TypeError, ValueError):
                bound = 20
        runs = [
            self._run_summary(manager, run)
            for run in manager.list_runs()[:bound]
        ]
        self.writer.emit("history_list", runs=runs)

    def research_readiness(self) -> None:
        result = evaluate_research_readiness(REPO_ROOT)
        self.writer.emit(
            "research_readiness",
            score=result["score"],
            status=result["status"],
            categories=result["categories"],
            blockers=result["blockers"],
        )

    def orchestrate(self, goal: str) -> None:
        if not goal.strip():
            self.writer.emit("log", level="warning", msg="/orchestrate requires a goal")
            return
        profile = detect_project(self.tool_repository_root)
        language = profile.language if profile else "python"
        weighted = tuple((name, weight) for name, weight in (
            ("qwen", self._contribution_split.qwen), ("api", self._contribution_split.api)
        ) if weight > 0)
        policy = ProviderPolicy(tuple(name for name, _ in weighted), tuple(weight for _, weight in weighted))
        graph = TaskGraph((
            TaskNode("research", "researcher", language, capabilities=("read",), provider_policy=policy,
                     inputs={"goal": goal}),
            TaskNode("implement", "implementer", language, dependencies=("research",),
                     capabilities=("read", "write", "command"), provider_policy=policy,
                     inputs={"goal": goal}),
            TaskNode("validate", "validator", language, dependencies=("implement",),
                     capabilities=("read", "command"), provider_policy=policy,
                     inputs={"goal": goal}),
        ))
        state = self._orchestration_store.create(graph, goal=goal)
        self._active_orchestration_id = state["session_id"]
        self.writer.emit("graph_proposal", session_id=state["session_id"], goal=goal,
                         revision=state["revision"]["revision"],
                         revision_hash=state["revision"]["revision_hash"],
                         graph=state["revision"]["graph"])

    def approve_graph(self, session_id: str, revision_hash: str) -> None:
        try:
            state = self._orchestration_store.approve(session_id, revision_hash)
        except (KeyError, ValueError) as exc:
            self.writer.emit("log", level="error", msg=f"graph approval failed: {exc}")
            return
        self._active_orchestration_id = session_id
        self.writer.emit("orchestration_state", state=state)

    def orchestration_action(self, command: dict[str, Any]) -> None:
        session_id = str(command.get("session_id") or self._active_orchestration_id or "")
        action = str(command.get("action") or "inspect")
        try:
            if action in {"start", "resume"}:
                state = self._orchestration_store.transition(session_id, "running")
            elif action == "pause":
                state = self._orchestration_store.transition(session_id, "paused")
            elif action == "cancel":
                state = self._orchestration_store.transition(session_id, "cancelled")
            elif action == "retry":
                provider = command.get("provider")
                state = self._orchestration_store.retry(session_id, str(command.get("node_id") or ""),
                                                        str(provider) if provider else None)
            elif action in {"break", "clear_break", "step"}:
                state = self._orchestration_store.debug_control(
                    session_id, action, str(command.get("node_id") or "")
                )
            else:
                state = self._orchestration_store.get(session_id)
            self.writer.emit("orchestration_state", state=state)
        except (KeyError, ValueError, PermissionError) as exc:
            self.writer.emit("log", level="error", msg=f"orchestration action failed: {exc}")

    def inspect_orchestration(self, session_id: str) -> None:
        self.orchestration_action({"session_id": session_id or self._active_orchestration_id, "action": "inspect"})

    def replay_orchestration(self, session_id: str) -> None:
        session_id = session_id or self._active_orchestration_id or ""
        try:
            events = [asdict(event) for event in self._orchestration_store.journal(session_id).replay()]
        except (KeyError, OSError, ValueError) as exc:
            self.writer.emit("log", level="error", msg=f"orchestration replay failed: {exc}")
            return
        self.writer.emit("orchestration_replay", session_id=session_id, external_actions=False, events=events)

    def repair_session_action(self, command: dict[str, Any]) -> None:
        action = str(command.get("action") or "")
        run_id = str(command.get("run_id") or "")
        entrypoint = str(command.get("entrypoint") or "coding_capability")
        if action in {"approve_patch", "reject_patch"}:
            self.resolve_tool_diff(action == "approve_patch")
            return
        if action == "cancel":
            self.cancel()
            return
        if action in {"continue", "retry", "resume", "escalate"}:
            if not run_id:
                self.writer.emit("log", level="error", msg="repair action requires run_id")
                return
            args = ["--resume-run", run_id]
            if entrypoint == "structured_spec":
                try:
                    checkpoint = ArtifactManager(self.artifact_root).load_checkpoint(run_id) or {}
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    self.writer.emit(
                        "log",
                        level="error",
                        msg=f"could not load repair checkpoint: {exc}",
                    )
                    return
                spec_path = str(checkpoint.get("spec_path") or "")
                if not spec_path:
                    self.writer.emit(
                        "log",
                        level="error",
                        msg="structured-spec repair action requires a checkpoint spec_path",
                    )
                    return
                args.extend(["--spec", spec_path])
            if action == "escalate":
                args.extend(["--architect-after-repair-attempts", "0"])
            self.start_run(entrypoint, args)
            return
        self.writer.emit("log", level="error", msg=f"unknown repair action: {action}")

    def _run_summary(self, manager: ArtifactManager, run_id: str) -> dict[str, Any]:
        try:
            checkpoint = manager.load_checkpoint(run_id) or {}
        except (ValueError, json.JSONDecodeError, OSError):
            checkpoint = {}
        session = checkpoint.get("session") or checkpoint
        attempts = session.get("attempts") or []
        return {
            "run_id": run_id,
            "target": str(session.get("target", "")),
            "final_status": str(session.get("final_status", "")),
            "attempt_count": len(attempts),
        }

    def _forward_run(
        self,
        process: subprocess.Popen[str],
        event_thread: threading.Thread | None = None,
        cleanup_path: Path | None = None,
    ) -> None:
        assert process.stdout is not None
        json_lines: list[str] = []
        for line in process.stdout:
            text = line.rstrip()
            if text:
                event = _contract_event_from_line(text)
                if event is not None:
                    self.writer.emit_event(event)
                if json_lines or text.lstrip().startswith("{"):
                    json_lines.append(text)
                    try:
                        payload = json.loads("\n".join(json_lines))
                    except json.JSONDecodeError:
                        if len(json_lines) > 10_000:
                            json_lines.clear()
                    else:
                        error_event = _architect_error_event(payload)
                        if error_event is not None:
                            self.writer.emit_event(error_event)
                        json_lines.clear()
                self.writer.emit("log", level="info", msg=text)
        returncode = process.wait()
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)
        if event_thread is not None:
            event_thread.join(timeout=2.0)
        status = "completed" if returncode == 0 else "failed"
        self.writer.emit("engine_progress", engine="harness", pct=100)
        self.writer.emit("done", status=status)

    def _forward_events(self, read_fd: int) -> None:
        with os.fdopen(read_fd, "r", encoding="utf-8") as stream:
            for raw_line in stream:
                try:
                    event = json.loads(raw_line)
                    if not isinstance(event, dict) or "type" not in event:
                        raise ValueError("event must be an object with a type")
                except (json.JSONDecodeError, ValueError) as exc:
                    self.writer.emit(
                        "log",
                        level="warning",
                        msg=f"invalid harness event: {exc}",
                    )
                    continue
                self.writer.emit_event(event)


def _validated_args(args: list[str]) -> list[str]:
    validated: list[str] = []
    index = 0
    while index < len(args):
        item = args[index]
        if not item.startswith("--"):
            raise ValueError(f"unexpected positional argument: {item}")
        flag = item.split("=", 1)[0]
        if flag not in ALLOWED_FLAGS:
            raise ValueError(f"unsupported harness flag: {flag}")
        validated.append(item)
        if "=" not in item and index + 1 < len(args) and not args[index + 1].startswith("--"):
            validated.append(args[index + 1])
            index += 1
        index += 1
    return validated


def _dotenv_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _provider_probe_url(settings: ProviderSettings) -> str:
    endpoint = settings.endpoint.rstrip("/")
    if settings.provider is Provider.QWEN:
        return endpoint if endpoint.endswith("/api/tags") else f"{endpoint}/api/tags"
    for suffix in ("/chat/completions", "/responses"):
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
            break
    return endpoint if endpoint.endswith("/models") else f"{endpoint}/models"


def _is_filesystem_mutation_request(message: str) -> bool:
    lowered = message.casefold()
    filesystem_cues = (
        "create a file",
        "create file",
        "create a directory",
        "create directory",
        "make a directory",
        "make directory",
        "mkdir ",
        "write a file",
        "add a file",
        "delete file",
        "delete a file",
        "remove file",
        "remove a file",
        "rename file",
        "rename a file",
        "move file",
        "move a file",
        "rearrange files",
        "touch ",
    )
    return any(cue in lowered for cue in filesystem_cues)


def _is_github_actions_request(message: str) -> bool:
    """Recognize GitHub workflow work without conflating it with approval."""

    lowered = message.casefold()
    return bool(
        re.search(
            r"\b(?:push|publish|commit|merge|open|create)\b.{0,40}"
            r"\b(?:github|pull request|pr|branch|workflow|action)\b",
            lowered,
        )
        or re.search(
            r"\b(?:github|pull request|pr|branch|workflow|action)\b.{0,40}"
            r"\b(?:push|publish|commit|merge|open|create)\b",
            lowered,
        )
    )


def _is_local_eligible_task(message: str) -> bool:
    """Keep Qwen on cheap, bounded repository chores; use DeepSeek otherwise."""

    return _is_filesystem_mutation_request(message) or _is_github_actions_request(message)


def _is_repository_maintenance_request(message: str) -> bool:
    """Route existing-code maintenance through inspect-then-propose tools."""

    lowered = message.casefold()
    return bool(
        re.search(
            r"\b(?:fix|debug|refactor|restructure|reorganize|reorganise|review|inspect|explain)\b"
            r".{0,56}\b(?:code|file|files|module|package|repository|repo|function|class|parser)\b",
            lowered,
        )
    )


def _is_code_generation_request(message: str) -> bool:
    """Recognize a request for a new code draft rather than a tool operation."""

    if _is_local_eligible_task(message) or _is_repository_maintenance_request(message):
        return False
    lowered = message.casefold()
    return bool(
        re.search(
            r"\b(?:create|build|make|write|implement|generate)\b.{0,80}"
            r"\b(?:code|function|class|app|application|api|cli|script|program|game|website|web\s+app|feature|module|package|library|bot)\b",
            lowered,
        )
    )


def _code_excerpt(content: str, max_lines: int = 32) -> tuple[str, int, bool]:
    """Return bounded source evidence suitable for a readable terminal stream."""

    lines = content.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(
            f"{number:>4} │ {line}" for number, line in enumerate(lines, start=1)
        ), 1, False
    head_lines = max(1, max_lines - 8)
    tail_lines = 6
    rendered = [
        *(f"{number:>4} │ {line}" for number, line in enumerate(lines[:head_lines], start=1)),
        f"     │ … {len(lines) - head_lines - tail_lines} line(s) omitted …",
        *(
            f"{number:>4} │ {line}"
            for number, line in enumerate(lines[-tail_lines:], start=len(lines) - tail_lines + 1)
        ),
    ]
    return "\n".join(rendered), 1, True


def _action_approval_reason(message: str) -> str | None:
    """Return a human-facing reason when a request can alter the repository."""

    lowered = message.casefold()
    if _is_github_actions_request(message):
        return "GitHub actions can publish or change repository history."
    if re.search(
        r"\b(?:refactor|restructure|reorganize|reorganise|rename|move)\b.{0,48}\b(?:code|repo|repository|file|files|folder|directory|module)\b",
        lowered,
    ):
        return "Restructuring can change several files and imports."
    if re.search(
        r"\b(?:delete|remove)\b.{0,48}\b(?:file|files|folder|directory|module|repo|repository)\b",
        lowered,
    ):
        return "Deleting files or directories is destructive."
    return None


def _host_operating_system() -> str:
    system = platform.system()
    return {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(
        system,
        system or "unknown",
    )


def _should_start_planning(message: str) -> bool:
    """Require an explicit software-planning request before opening spec review."""
    lowered = message.casefold()
    # A direct repository operation is a tool task, not a request to design a
    # project.  In particular, "create a file/function" must reach the local
    # Qwen tool loop instead of unexpectedly opening the DeepSeek spec intake.
    if re.search(
        r"\b(?:create|add|remove|delete|rename|move|edit|update|write|change)\s+"
        r"(?:a|an|the)?\s*(?:file|folder|directory|function|class|module)\b",
        lowered,
    ):
        return False
    # Planning is a deliberate mode. Vague requests such as "build a CLI" go
    # to the local generation/tool loop; `/spec` or explicit planning language
    # opts into the architect questionnaire and contract workflow.
    if not re.search(
        r"\b(?:plan|planning|spec(?:ification)?|architect(?:ure)?|design)\b",
        lowered,
    ):
        return False
    coding_targets = (
        "software",
        "code",
        "coding",
        "app",
        "application",
        "api",
        "cli",
        "command line",
        "command-line",
        "tui",
        "website",
        "web app",
        "script",
        "program",
        "game",
        "repository",
        "repo",
        "feature",
        "function",
        "module",
        "package",
        "library",
        "database",
        "bot",
        "automation",
        "terminal",
        "task manager",
        "task tracker",
        "tracker",
        "multi-file",
        "dashboard",
        "service",
    )
    return any(
        re.search(rf"\b{re.escape(target)}\b", lowered) is not None
        for target in coding_targets
    )


def _chat_transcript(history: list[dict[str, str]]) -> str:
    return "\n".join(
        f"{message.get('role', 'unknown').upper()}: {message.get('content', '')}"
        for message in history[-MAX_CHAT_MESSAGES:]
    )


def _preference_context(preferences: list[str]) -> str:
    if not preferences:
        return "USER PREFERENCES: none recorded"
    return "USER PREFERENCES:\n" + "\n".join(
        f"- {preference}" for preference in preferences[-MAX_PREFERENCES:]
    )


def _research_slug(question: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", question.strip().lower()).strip("-")
    return (slug or "research")[:60]


def _save_research_doc(directory: Path, question: str, document: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    path = directory / f"{stamp}_{_research_slug(question)}.md"
    path.write_text(document, encoding="utf-8")
    return path


def _decode_json_object(response: str, label: str) -> dict[str, Any]:
    raw = response.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[1:-1]).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _render_research_doc(question: str, response: str) -> str:
    payload = _decode_json_object(response, "research artifact")
    relevant_files = _sheet_list(payload.get("relevant_files"), "relevant_files", allow_empty=True)
    code_flow = _sheet_list(payload.get("code_flow"), "code_flow", allow_empty=True)
    open_questions = _sheet_list(payload.get("open_questions"), "open_questions", allow_empty=True)
    hypothesis = " ".join(str(payload.get("hypothesis") or "").split()) or "(inconclusive)"
    lines = [
        "# Research",
        "",
        "## Question",
        "",
        _sheet_text(payload.get("question") or question, "question"),
        "",
        "## Relevant Files",
        "",
        *(f"- {item}" for item in relevant_files),
        "",
        "## Code Flow",
        "",
        *(f"{index}. {item}" for index, item in enumerate(code_flow, start=1)),
        "",
        "## Hypothesis",
        "",
        hypothesis,
        "",
        "## Open Questions",
        "",
        *(f"- {item}" for item in open_questions),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _render_compaction_summary(response: str) -> str:
    payload = _decode_json_object(response, "compaction summary")
    lines = [
        f"Goal: {_sheet_text(payload.get('goal'), 'goal')}",
        f"Approach: {_sheet_text(payload.get('approach'), 'approach')}",
        "Done:",
        *(f"  - {item}" for item in _sheet_list(payload.get("done"), "done", allow_empty=True)),
        "Current blocker: "
        + (" ".join(str(payload.get("current_blocker") or "").split()) or "(none)"),
        "Key facts:",
        *(f"  - {item}" for item in _sheet_list(payload.get("key_facts"), "key_facts", allow_empty=True)),
        "Active diffs:",
        *(f"  - {item}" for item in _sheet_list(payload.get("active_diffs", []), "active_diffs", allow_empty=True)),
        "Contribution: " + (" ".join(str(payload.get("contribution_state") or "").split()) or "(unchanged)"),
        "Cost: " + (" ".join(str(payload.get("cost_state") or "").split()) or "(unchanged)"),
        "Checkpoints:",
        *(f"  - {item}" for item in _sheet_list(payload.get("checkpoint_ids", []), "checkpoint_ids", allow_empty=True)),
    ]
    return "\n".join(lines)


def _render_spec_sheet(response: str) -> str:
    """Validate a model-filled JSON sheet and render planner-owned Markdown."""

    raw = response.strip()
    if raw.startswith("```") and raw.endswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("DeepSeek returned an invalid execution spec sheet") from exc
    if not isinstance(payload, dict):
        raise ValueError("DeepSeek execution spec sheet must be a JSON object")

    app_spec = payload.get("app_spec")
    if not isinstance(app_spec, dict):
        raise ValueError("execution spec sheet is missing app_spec")
    name = _sheet_text(app_spec.get("name"), "app_spec.name")
    language = _sheet_text(app_spec.get("language"), "app_spec.language")
    kernel_mode = _sheet_text(
        app_spec.get("kernel_mode") or "generate_from_spec",
        "app_spec.kernel_mode",
    )
    libraries = _sheet_list(app_spec.get("libraries"), "app_spec.libraries", allow_empty=True)
    goal = _sheet_text(payload.get("goal"), "goal")
    files = _sheet_list(payload.get("files"), "files")
    components = _sheet_symbols(payload.get("required_components"), "required_components")
    entrypoints = _sheet_symbols(payload.get("entrypoints"), "entrypoints")
    dependency_graph = _sheet_dependencies(payload.get("dependency_graph"), components, entrypoints)

    sections: list[tuple[str, list[str]]] = [
        ("Goal", [goal]),
        ("Files", files),
        ("Required Components", [f"`{item}`" for item in components]),
        ("Entrypoint", [f"`{item}`" for item in entrypoints]),
        ("Dependency Graph", dependency_graph),
        ("State Rules", _sheet_list(payload.get("state_rules"), "state_rules")),
        ("Interfaces", _sheet_list(payload.get("interfaces"), "interfaces")),
        ("Constraints", _sheet_list(payload.get("constraints"), "constraints")),
        (
            "Acceptance Examples",
            _sheet_list(payload.get("acceptance_examples"), "acceptance_examples"),
        ),
        ("Validation", _sheet_list(payload.get("validation"), "validation")),
    ]
    app_lines = [
        f"- name: {name}",
        f"- language: {language}",
        f"- kernel_mode: {kernel_mode}",
    ]
    if libraries:
        app_lines.append(f"- libraries: {', '.join(libraries)}")
    lines = ["# Execution Spec Sheet", "", "## App Spec", "", *app_lines]
    for heading, values in sections:
        lines.extend(["", f"## {heading}", "", *(f"- {value}" for value in values)])
    return "\n".join(lines).rstrip() + "\n"


def _sheet_text(value: Any, field: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"execution spec sheet is missing {field}")
    return text


def _sheet_list(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"execution spec sheet field {field} must be a list")
    items = []
    for raw_item in value:
        item = " ".join(str(raw_item or "").split())
        if item and item not in items:
            items.append(item)
    if not items and not allow_empty:
        raise ValueError(f"execution spec sheet field {field} cannot be empty")
    return items


def _sheet_symbols(value: Any, field: str) -> list[str]:
    items = _sheet_list(value, field)
    symbols: list[str] = []
    for item in items:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(\(\))?", item.strip("`"))
        if match is None:
            raise ValueError(f"execution spec sheet field {field} contains an invalid symbol: {item}")
        symbol = match.group(1) + (match.group(2) or "")
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _sheet_dependencies(value: Any, components: list[str], entrypoints: list[str]) -> list[str]:
    items = _sheet_list(value, "dependency_graph", allow_empty=True)
    known = {item.removesuffix("()") for item in [*components, *entrypoints]}
    dependencies: list[str] = []
    for item in items:
        match = re.fullmatch(
            r"`?([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?`?\s*->\s*`?([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?`?",
            item,
        )
        if match is None or match.group(1) not in known or match.group(2) not in known:
            raise ValueError(f"execution spec sheet has an invalid dependency: {item}")
        dependency = f"{match.group(1)} -> {match.group(2)}"
        if dependency not in dependencies:
            dependencies.append(dependency)
    if dependencies:
        return dependencies
    first = components[0].removesuffix("()")
    last = entrypoints[-1].removesuffix("()")
    return [f"{first} -> {last}"] if first != last else []


def _parse_questionnaire_response(response: str) -> tuple[str, list[dict[str, Any]]]:
    raw = response.strip()
    if raw.startswith("```") and raw.endswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return response.strip(), []
    if not isinstance(payload, dict):
        return response.strip(), []
    message = str(payload.get("message") or "").strip()
    if str(payload.get("kind") or "").casefold() != "questionnaire":
        return message or response.strip(), []

    questions: list[dict[str, Any]] = []
    raw_questions = payload.get("questions")
    if isinstance(raw_questions, list):
        for raw_question in raw_questions[:MAX_QUESTIONS]:
            if not isinstance(raw_question, dict):
                continue
            question_text = str(raw_question.get("question_text") or "").strip()
            raw_options = raw_question.get("options")
            if not question_text or not isinstance(raw_options, list):
                continue
            option_texts: list[str] = []
            for raw_option in raw_options:
                option_value = (
                    raw_option.get("text", "")
                    if isinstance(raw_option, dict)
                    else raw_option
                )
                option = str(option_value or "").strip()
                if (
                    option
                    and option.casefold() != "other"
                    and option not in option_texts
                    and len(option_texts) < MAX_OPTIONS_PER_QUESTION - 1
                ):
                    option_texts.append(option)
            if len(option_texts) < 2:
                continue
            option_texts.append("Other")
            questions.append(
                {
                    "question_text": question_text,
                    "options": [
                        {"id": index, "text": option}
                        for index, option in enumerate(option_texts, start=1)
                    ],
                }
            )
    if len(questions) < 2:
        return message or "I need a little more context before drafting the spec.", []
    return message or "Choose the options that best match what you want to build.", questions


def _questionnaire_transcript(message: str, questions: list[dict[str, Any]]) -> str:
    lines = [message]
    for question in questions:
        lines.append(str(question["question_text"]))
        lines.extend(
            f"{option['id']}. {option['text']}" for option in question["options"]
        )
    return "\n".join(lines)


def _architect_error_event(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    error = str(payload.get("architect_contract_error") or "").strip()
    if not error:
        return None
    code = str(payload.get("architect_contract_error_code") or "").strip()
    fallback_used = bool(payload.get("architect_contracts_fallback_used"))
    if fallback_used:
        message = (
            f"DeepSeek contract planner response was unusable ({code or 'unknown'}): "
            f"{error}. Continuing with the validated spec-sheet contract queue."
        )
    elif code == "architect_contract_missing_api_key":
        message = (
            f"DeepSeek is not configured: {error}. Set DEEPSEEK_API_KEY in the "
            "repository .env file or export it in the launching shell."
        )
    else:
        message = f"DeepSeek contract planner error ({code or 'unknown'}): {error}"
    return {
        "type": "log",
        "level": "warning" if fallback_used else "error",
        "msg": message,
    }


def _contract_event_from_line(line: str) -> dict[str, Any] | None:
    if line.startswith("[contract-plan] "):
        try:
            payload = json.loads(line.removeprefix("[contract-plan] "))
        except json.JSONDecodeError:
            return None
        contracts = payload.get("contracts")
        if isinstance(contracts, list):
            return {"type": "contract_queue_planned", "contracts": contracts}
        return None

    match = re.match(
        r"^\[contract-queue\](?:\s+\d+/\d+)?\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*):\s+(?P<message>.+)$",
        line,
    )
    if match is None:
        return None
    message = match.group("message")
    status = "running"
    worker = "small_worker"
    attempt = 0
    retry_match = re.search(r"retry\s+(\d+)", message)
    if retry_match:
        status = "retrying"
        attempt = int(retry_match.group(1))
    elif "sending to small worker" in message:
        status = "dispatched"
    elif "escalating contract to architect" in message:
        status = "escalated"
        worker = "architect_llm"
    elif "accepted" in message:
        status = "accepted"
    elif "failed validation" in message:
        status = "validation_failed"
    elif "backend failed" in message:
        status = "backend_failed"
    elif "waiting on" in message:
        status = "waiting"
    return {
        "type": "contract_progress",
        "name": match.group("name"),
        "status": status,
        "attempt": attempt,
        "worker": worker,
    }


def main() -> int:
    # The Rust TUI passes its selected working directory explicitly.  Keeping
    # this separate from this module's source root prevents tool calls from
    # accidentally editing the harness when the user opens another project.
    repository_root = os.environ.get("HARNESS_REPOSITORY_ROOT")
    bridge = Bridge(tool_repository_root=repository_root)
    bridge.writer.emit("ready", protocol_version=PROTOCOL_VERSION)
    bridge.emit_startup_status()
    for raw_line in sys.stdin:
        try:
            command = json.loads(raw_line)
            if not isinstance(command, dict):
                raise ValueError("command must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            bridge.writer.emit(
                "log",
                level="warning",
                msg=f"invalid command: {exc}",
            )
            continue
        bridge.handle(command)
    bridge.cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
