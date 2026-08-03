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
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, TextIO

from agents.artifact_manager import ArtifactManager
from agents.repo_map_agent import RepoMapAgent
from agents.tool_calling_agent import ToolCallRecord, ToolCallingAgent
from backends.architect_client import ArchitectApiClient, ArchitectConfig
from backends.ollama_client import (
    DEFAULT_OLLAMA_MODEL,
    OllamaClient,
    OllamaGenerationConfig,
)
from engines.compilation_engine import CompilationEngine
from harness_kernel.compute_shield import ShieldTaskTokens, compute_shield_metrics
from harness_kernel.profiling import ProfileResult
from harness_kernel.event_stream import EVENT_FD_ENV
from harness_kernel.tool_handlers import (
    ApplySearchReplaceResponse,
    apply_reviewed_search_replace,
    build_default_tool_registry,
)
from TUI.mermaid_renderer import render_repo_architecture_mermaid
from TUI.mermaid_renderer import render_repo_architecture


PROTOCOL_VERSION = 4
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY_PATH = REPO_ROOT / ".tui_memory.json"
MAX_CHAT_MESSAGES = 24
MAX_PREFERENCES = 50
MAX_QUESTIONS = 4
MAX_OPTIONS_PER_QUESTION = 5
QUESTIONNAIRE_SYSTEM_PROMPT = """You are an autonomous planning worker and software architect.
When the latest user message introduces or materially changes a software project idea, do not write code and do not claim to execute anything. Return JSON only in this shape:
{"kind":"questionnaire","message":"short introduction","questions":[{"question_text":"...","options":["...","..."]}]}
Ask 2 to 4 high-impact clarification questions. Give each question 2 to 4 concise, mutually distinct choices. Do not include Other; the application adds it. Focus on behavior, scope, constraints, data, interfaces, and acceptance criteria.
For greetings, ordinary conversation, preference updates, or answers that do not require a new questionnaire, return:
{"kind":"chat","message":"your concise response"}
Never return markdown fences around the JSON. The application creates a formal spec only after the questionnaire is completed or the user explicitly requests a draft."""
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
    ) -> None:
        self.writer = writer or EventWriter()
        self.artifact_root = (
            Path(artifact_root)
            if artifact_root is not None
            else REPO_ROOT / "artifacts" / "runs"
        )
        self.env_file = Path(env_file) if env_file is not None else REPO_ROOT / ".env"
        self.memory_path = (
            Path(memory_path) if memory_path is not None else DEFAULT_MEMORY_PATH
        )
        self._architect_client = architect_client
        self._tool_generate_text = tool_generate_text
        self.tool_repository_root = Path(tool_repository_root or REPO_ROOT).resolve()
        self._pending_tool_diff: ApplySearchReplaceResponse | None = None
        self._chat_history: list[dict[str, str]] = []
        self._assistant_busy = False
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def emit_startup_status(self) -> None:
        child_env = self._child_environment()
        configured = bool(
            child_env.get("ARCHITECT_API_KEY", "").strip()
            or child_env.get("DEEPSEEK_API_KEY", "").strip()
        )
        source = self._architect_key_source()
        self.writer.emit(
            "config_status",
            deepseek_configured=configured,
            source=source,
            memory_path=str(self.memory_path),
            preference_count=len(self._load_preferences()),
        )
        if not configured:
            self.writer.emit(
                "log",
                level="warning",
                msg=(
                    "DeepSeek is not configured. Set DEEPSEEK_API_KEY in the "
                    "repository .env file or export it before sending a prompt."
                ),
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
        elif kind == "questionnaire_complete":
            self.complete_questionnaire(command.get("answers"))
        elif kind == "execute_spec":
            self.start_spec_execution(str(command.get("text") or ""))
        elif kind == "tool_task":
            self.start_tool_task(
                str(command.get("text") or ""),
                str(command.get("provider") or "qwen"),
            )
        elif kind == "apply_tool_diff":
            self.resolve_tool_diff(bool(command.get("approved")))
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
        else:
            self.writer.emit("log", level="error", msg=f"unknown command: {kind}")

    def start_chat(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.writer.emit("log", level="warning", msg="chat message cannot be empty")
            return
        if self._assistant_busy:
            self.writer.emit("log", level="warning", msg="DeepSeek is already responding")
            return
        self._chat_history.append({"role": "user", "content": text})
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
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

    def start_tool_task(self, text: str, provider: str = "qwen") -> None:
        task = text.strip()
        if not task:
            self.writer.emit("log", level="warning", msg="tool task cannot be empty")
            return
        if self._assistant_busy:
            self.writer.emit("log", level="warning", msg="an assistant task is already running")
            return
        selected_provider = provider.strip().lower()
        if selected_provider not in {"qwen", "deepseek"}:
            self.writer.emit(
                "log",
                level="error",
                msg=f"unsupported tool provider: {provider}",
            )
            return
        self._pending_tool_diff = None
        self.writer.emit("chat_message", role="user", content=f"[repository task] {task}")
        self._start_assistant_task(
            "repository_tools",
            lambda: self._run_tool_task(task, selected_provider),
        )

    def _run_tool_task(self, task: str, provider: str) -> None:
        proposals: list[ApplySearchReplaceResponse] = []

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
            if isinstance(raw_value, ApplySearchReplaceResponse):
                proposals.append(raw_value)

        run = ToolCallingAgent(
            self._tool_generator(provider),
            build_default_tool_registry(repository_root=self.tool_repository_root),
            max_turns=8,
            on_tool_result=on_tool_result,
        ).run(task)
        self.writer.emit(
            "tool_answer",
            answer=run.final_answer,
            exhausted=run.exhausted,
            call_count=len(run.calls),
        )
        if proposals:
            if len(proposals) > 1:
                self.writer.emit(
                    "log",
                    level="warning",
                    msg="multiple diffs were proposed; only the latest is pending review",
                )
            self._pending_tool_diff = proposals[-1]
            proposal = self._pending_tool_diff
            self.writer.emit(
                "tool_diff",
                path=proposal.path,
                diff=proposal.diff,
                replacements=proposal.replacements,
            )

    def _tool_generator(self, provider: str):
        if self._tool_generate_text is not None:
            return self._tool_generate_text
        if provider == "deepseek":
            return lambda prompt: self._client().generate(
                prompt,
                system="Return one repository tool-call JSON object only.",
            )
        client = OllamaClient()
        return lambda prompt: client.generate(
            prompt,
            model=DEFAULT_OLLAMA_MODEL,
            config=OllamaGenerationConfig(
                temperature=0.0,
                num_predict=1200,
                num_ctx=8192,
            ),
            system="Return one repository tool-call JSON object only.",
        )

    def resolve_tool_diff(self, approved: bool) -> None:
        proposal = self._pending_tool_diff
        if proposal is None:
            self.writer.emit("log", level="warning", msg="no tool diff is pending review")
            return
        try:
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
        self._pending_tool_diff = None
        self.writer.emit(
            "tool_diff_resolved",
            path=result.path,
            applied=result.applied,
            message="diff applied" if result.applied else "diff discarded",
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
        response = self._client().generate(
            self._chat_prompt(),
            system=QUESTIONNAIRE_SYSTEM_PROMPT,
        ).strip()
        message, questions = _parse_questionnaire_response(response)
        history_content = (
            _questionnaire_transcript(message, questions) if questions else message
        )
        self._chat_history.append({"role": "assistant", "content": history_content})
        self._chat_history = self._chat_history[-MAX_CHAT_MESSAGES:]
        self.writer.emit("chat_message", role="assistant", content=message)
        if questions:
            self.writer.emit("questionnaire", questions=questions)

    def _run_spec_draft(self) -> None:
        response = self._client().generate(
            self._spec_prompt(),
            system=SPEC_SHEET_SYSTEM_PROMPT,
        ).strip()
        self.writer.emit("spec_draft", text=_render_spec_sheet(response))

    def _client(self) -> ArchitectApiClient:
        if self._architect_client is None:
            self._architect_client = ArchitectApiClient(
                ArchitectConfig(env_file=str(self.env_file))
            )
        return self._architect_client

    def _chat_prompt(self) -> str:
        return "\n\n".join(
            [
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
        for key in ("ARCHITECT_API_KEY", "DEEPSEEK_API_KEY"):
            if os.environ.get(key, "").strip():
                return f"environment:{key}"
        values = _dotenv_values(self.env_file)
        for key in ("ARCHITECT_API_KEY", "DEEPSEEK_API_KEY"):
            if values.get(key, "").strip():
                return f".env:{key}"
        return "missing"

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
        return child_env

    def cancel(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            self.writer.emit("log", level="warning", msg="cancellation requested")

    def repo_map(self, root: str, focus: str, mode: str = "diagram") -> None:
        try:
            root_path = Path(root).resolve()
            graph = RepoMapAgent().map_repo(root_path)
            diagram = render_repo_architecture_mermaid(graph, focus=focus)
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
            mermaid=diagram,
            summary=render_repo_architecture(graph, focus=focus),
        )

    def compile_source(self, language: str, source: str) -> None:
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

    def _run_summary(self, manager: ArtifactManager, run_id: str) -> dict[str, Any]:
        try:
            checkpoint = manager.load_checkpoint(run_id) or {}
        except (ValueError, json.JSONDecodeError, OSError):
            checkpoint = {}
        attempts = checkpoint.get("attempts") or []
        return {
            "run_id": run_id,
            "target": str(checkpoint.get("target", "")),
            "final_status": str(checkpoint.get("final_status", "")),
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
    bridge = Bridge()
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
