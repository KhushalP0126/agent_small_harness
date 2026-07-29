"""JSON-lines subprocess bridge for the Rust TUI.

The bridge deliberately launches existing CLI entrypoints instead of importing
``GenerationController``. JSON is reserved for stdout; child output is wrapped
as structured log events so malformed human-readable lines cannot corrupt the
protocol.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, TextIO

from agents.repo_map_agent import RepoMapAgent
from engines.compilation_engine import CompilationEngine
from harness_kernel.compute_shield import ShieldTaskTokens, compute_shield_metrics
from harness_kernel.profiling import ProfileResult
from harness_kernel.event_stream import EVENT_FD_ENV
from TUI.mermaid_renderer import render_repo_architecture_mermaid


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
    def __init__(self, writer: EventWriter | None = None) -> None:
        self.writer = writer or EventWriter()
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def handle(self, command: dict[str, Any]) -> None:
        kind = str(command.get("cmd") or "")
        if kind == "run":
            self.start_run(
                str(command.get("entrypoint") or ""),
                [str(item) for item in command.get("args") or []],
            )
        elif kind == "cancel":
            self.cancel()
        elif kind == "repo_map":
            self.repo_map(
                str(command.get("root") or REPO_ROOT),
                str(command.get("focus") or ""),
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
        else:
            self.writer.emit("log", level="error", msg=f"unknown command: {kind}")

    def start_run(self, entrypoint: str, args: list[str]) -> None:
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
            args=(process, event_thread),
            daemon=True,
        ).start()

    def cancel(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            self.writer.emit("log", level="warning", msg="cancellation requested")

    def repo_map(self, root: str, focus: str) -> None:
        try:
            graph = RepoMapAgent().map_repo(Path(root).resolve())
            diagram = render_repo_architecture_mermaid(graph, focus=focus)
        except Exception as exc:  # noqa: BLE001 - protocol boundary
            self.writer.emit(
                "log",
                level="error",
                msg=f"repository map failed: {type(exc).__name__}: {exc}",
            )
            return
        self.writer.emit("repo_map", mermaid=diagram)

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

    def _forward_run(
        self,
        process: subprocess.Popen[str],
        event_thread: threading.Thread | None = None,
    ) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip()
            if text:
                self.writer.emit("log", level="info", msg=text)
        returncode = process.wait()
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
