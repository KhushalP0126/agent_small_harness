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
from engines.compilation_engine import CompilationEngine
from harness_kernel.compute_shield import ShieldTaskTokens, compute_shield_metrics
from harness_kernel.profiling import ProfileResult
from harness_kernel.event_stream import EVENT_FD_ENV
from TUI.mermaid_renderer import render_repo_architecture_mermaid
from TUI.mermaid_renderer import render_repo_architecture


PROTOCOL_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[1]
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
    ) -> None:
        self.writer = writer or EventWriter()
        self.artifact_root = (
            Path(artifact_root)
            if artifact_root is not None
            else REPO_ROOT / "artifacts" / "runs"
        )
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def handle(self, command: dict[str, Any]) -> None:
        kind = str(command.get("cmd") or "")
        if kind == "run":
            self.start_run(
                str(command.get("entrypoint") or ""),
                [str(item) for item in command.get("args") or []],
            )
        elif kind == "prompt":
            self.start_prompt(str(command.get("text") or ""))
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

    def start_prompt(self, text: str) -> None:
        if not text.strip():
            self.writer.emit("log", level="warning", msg="prompt cannot be empty")
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
            child_env = os.environ.copy()
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
            lines = []
            for record in graph.files:
                imports = sorted({item.module for item in record.imports if item.module})
                variables = sorted({item.name for item in record.variables})
                if imports or variables:
                    lines.append(record.path)
                    lines.append(f"  imports: {', '.join(imports) or 'none'}")
                    lines.append(f"  variables: {', '.join(variables) or 'none'}")
            self.writer.emit(
                "repo_map_view",
                mode=mode,
                content="\n".join(lines) or "No imports or variables found.",
            )
            return
        if mode == "files":
            lines = []
            for record in graph.files:
                symbols = [*record.classes, *(item.name for item in record.functions)]
                lines.append(record.path)
                summary = ""
                try:
                    source = (root_path / record.path).read_text(encoding="utf-8")
                    summary = (ast.get_docstring(ast.parse(source)) or "").splitlines()[0]
                except (OSError, UnicodeDecodeError, SyntaxError, IndexError):
                    summary = ""
                if not summary:
                    summary = f"Defines {', '.join(symbols[:8])}." if symbols else "No module-level classes or functions."
                lines.append(f"  {summary}")
            self.writer.emit("repo_map_view", mode=mode, content="\n".join(lines))
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
        for line in process.stdout:
            text = line.rstrip()
            if text:
                event = _contract_event_from_line(text)
                if event is not None:
                    self.writer.emit_event(event)
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
