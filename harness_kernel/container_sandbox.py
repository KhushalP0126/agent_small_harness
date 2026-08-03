"""Container and explicit local execution for generated source artifacts."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from harness_kernel.language_adapters import get_language_adapter
from harness_kernel.local_sandbox import MAX_CAPTURE_BYTES, sanitized_environment
from harness_kernel.tool_registry import ToolError


CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]


@dataclass(frozen=True)
class SandboxResult:
    language: str
    mode: str
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool


def container_command(
    scratch_dir: Path,
    language: str,
    *,
    runtime: str = "docker",
    network_enabled: bool = False,
) -> list[str]:
    adapter = get_language_adapter(language)
    if runtime not in {"docker", "podman"}:
        raise ToolError(f"Unsupported container runtime: {runtime}", kind="invalid_runtime")
    command = [
        runtime,
        "run",
        "--rm",
        "--interactive",
        "--read-only",
        "--network",
        "bridge" if network_enabled else "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "512m",
        "--cpus",
        "1.0",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--mount",
        f"type=bind,src={scratch_dir.resolve()},dst=/workspace,rw",
        "--workdir",
        "/workspace",
        adapter.container_image,
        *adapter.container_command,
    ]
    return command


def run_source_isolated(
    source: str,
    language: str,
    *,
    timeout_seconds: float = 10.0,
    mode: str = "container",
    runtime: str = "docker",
    network_enabled: bool = False,
    allow_local_fallback: bool = False,
    runner: CommandRunner = subprocess.run,
) -> SandboxResult:
    """Execute source with no implicit fallback across a security boundary."""

    adapter = get_language_adapter(language)
    if mode not in {"container", "local"}:
        raise ToolError(f"Unsupported sandbox mode: {mode}", kind="invalid_sandbox_mode")
    with tempfile.TemporaryDirectory(prefix="agent-harness-exec-") as temp_dir:
        scratch = Path(temp_dir)
        (scratch / adapter.filename).write_text(source, encoding="utf-8")
        if mode == "container":
            if shutil.which(runtime) is None:
                if not allow_local_fallback:
                    raise ToolError(
                        f"Container runtime {runtime!r} is unavailable; local fallback requires explicit approval",
                        kind="container_unavailable",
                    )
                return _run_local(runner, adapter, scratch, timeout_seconds)
            command = container_command(
                scratch,
                adapter.language,
                runtime=runtime,
                network_enabled=network_enabled,
            )
            return _run_command(runner, command, scratch, timeout_seconds, "container", adapter.language)
        return _run_local(runner, adapter, scratch, timeout_seconds)


def _run_local(runner, adapter, scratch: Path, timeout_seconds: float) -> SandboxResult:
    return _run_command(
        runner,
        list(adapter.local_command),
        scratch,
        timeout_seconds,
        "local",
        adapter.language,
        environment=sanitized_environment(scratch),
    )


def _run_command(
    runner: CommandRunner,
    command: Sequence[str],
    scratch: Path,
    timeout_seconds: float,
    mode: str,
    language: str,
    environment: dict[str, str] | None = None,
) -> SandboxResult:
    try:
        completed = runner(
            list(command),
            cwd=scratch,
            env=environment,
            input=b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(0.1, min(float(timeout_seconds), 120.0)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxResult(
            language,
            mode,
            None,
            _bounded(exc.stdout or b""),
            _bounded(exc.stderr or b""),
            True,
        )
    return SandboxResult(
        language,
        mode,
        completed.returncode,
        _bounded(completed.stdout),
        _bounded(completed.stderr),
        False,
    )


def _bounded(payload: bytes | str) -> str:
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    return raw[-MAX_CAPTURE_BYTES:].decode("utf-8", errors="replace")
