"""Defense-in-depth local execution for generated Python.

This boundary deliberately does not call itself a hardened security sandbox:
without a container or OS policy it cannot prevent access to absolute host paths
or the network. It does keep generated code out of the harness process, removes
repository secrets from the child environment, uses a disposable working
directory, bounds captured output, and terminates the whole child process group
on timeout.
"""

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


MAX_CAPTURE_BYTES = 64 * 1024
DEFAULT_MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
DEFAULT_FILE_LIMIT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class LocalSandboxResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    working_directory: str


def sanitized_environment(scratch_dir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return the small environment exposed to generated code."""

    environment = {
        "HOME": str(scratch_dir),
        "TMPDIR": str(scratch_dir),
        "TMP": str(scratch_dir),
        "TEMP": str(scratch_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
    }
    for key in ("LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    environment["PATH"] = os.defpath
    if extra:
        environment.update({str(key): str(value) for key, value in extra.items()})
    return environment


def run_python_locally_isolated(
    source: str,
    *,
    timeout_seconds: float,
    extra_environment: dict[str, str] | None = None,
) -> LocalSandboxResult:
    """Execute Python outside the harness process with bounded local resources."""

    with tempfile.TemporaryDirectory(prefix="agent-harness-sandbox-") as temp_dir:
        scratch_dir = Path(temp_dir)
        candidate_path = scratch_dir / "candidate.py"
        runner_path = scratch_dir / "runner.py"
        candidate_path.write_text(source, encoding="utf-8")
        runner_path.write_text(_runner_source(candidate_path, timeout_seconds), encoding="utf-8")

        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                [sys.executable, "-I", str(runner_path)],
                cwd=scratch_dir,
                env=sanitized_environment(scratch_dir, extra_environment),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=os.name == "posix",
            )
            timed_out = False
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_tree(process)
            else:
                _terminate_remaining_process_group(process.pid)
            stdout = _read_tail(stdout_file)
            stderr = _read_tail(stderr_file)
            return LocalSandboxResult(
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                working_directory=str(scratch_dir),
            )


def _runner_source(candidate_path: Path, timeout_seconds: float) -> str:
    cpu_seconds = max(1, math.ceil(timeout_seconds) + 1)
    return f"""
import runpy

try:
    import resource
except ImportError:
    resource = None

if resource is not None:
    limits = [
        (resource.RLIMIT_CPU, {cpu_seconds}, {cpu_seconds}),
        (resource.RLIMIT_FSIZE, {DEFAULT_FILE_LIMIT_BYTES}, {DEFAULT_FILE_LIMIT_BYTES}),
        (resource.RLIMIT_CORE, 0, 0),
    ]
    if hasattr(resource, "RLIMIT_AS"):
        limits.append((resource.RLIMIT_AS, {DEFAULT_MEMORY_LIMIT_BYTES}, {DEFAULT_MEMORY_LIMIT_BYTES}))
    for limit, soft, hard in limits:
        try:
            resource.setrlimit(limit, (soft, hard))
        except (OSError, ValueError):
            pass

runpy.run_path({str(candidate_path)!r}, run_name="__main__")
""".lstrip()


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        _terminate_remaining_process_group(process.pid)
    else:
        process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _terminate_remaining_process_group(process_group_id: int) -> None:
    if os.name != "posix":
        return
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _read_tail(handle) -> str:
    handle.flush()
    size = handle.seek(0, os.SEEK_END)
    handle.seek(max(0, size - MAX_CAPTURE_BYTES))
    return handle.read().decode("utf-8", errors="replace")
