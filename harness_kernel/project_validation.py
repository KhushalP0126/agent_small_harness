"""Canonical, capability-honest project validation for supported languages."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from harness_kernel.container_sandbox import (
    ALLOWED_CONTAINER_RUNTIMES,
    ContainerFilesystemPolicy,
    ContainerMount,
    SandboxResult,
    _assert_command_obeys_policy,
    _run_command,
)
from harness_kernel.language_adapters import LanguageProfile, get_language_profile
from harness_kernel.local_sandbox import sanitized_environment
from harness_kernel.tool_registry import ToolError


@dataclass(frozen=True)
class ValidationStep:
    capability: str
    command: tuple[str, ...]
    status: str  # passed | failed | unavailable | skipped
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@dataclass(frozen=True)
class ProjectValidationResult:
    language: str
    mode: str
    tier: str  # full | partial | unavailable
    passed: bool
    steps: tuple[ValidationStep, ...]
    network_enabled: bool
    trusted_tests_read_only: bool


Runner = Callable[..., subprocess.CompletedProcess[bytes]]


def canonical_commands(profile: LanguageProfile, root: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return fixed commands only; repository manifests never inject argv."""
    if profile.language in {"c", "cpp"}:
        configure = ("cmake", "-S", ".", "-B", "build")
        return (("build", configure), ("build", profile.build_command), ("test", profile.test_command))
    commands = [("build", profile.build_command), ("test", profile.test_command)]
    if profile.lint_command:
        # JavaScript lint is only a gate when the manifest declares that script.
        if profile.language != "javascript" or _package_has_script(root, "lint"):
            commands.append(("lint", profile.lint_command))
    return tuple((capability, command) for capability, command in commands if command)


def validate_project(
    root: Path,
    language: str,
    *,
    mode: str = "container",
    runtime: str = "docker",
    trusted_tests: Path | None = None,
    network_enabled: bool = False,
    network_approved: bool = False,
    timeout_seconds: float = 120.0,
    runner: Runner = subprocess.run,
) -> ProjectValidationResult:
    """Run canonical gates without treating unavailable tools as passing.

    Network use is a separate approval. Trusted tests are accepted only as a
    distinct read-only directory; agent workspace tests are never promoted by
    this interface.
    """
    project = root.resolve()
    profile = get_language_profile(language)
    if not any((project / marker).is_file() for marker in profile.project_markers):
        raise ToolError(f"{profile.language} project marker is missing", kind="project_not_detected")
    if network_enabled and not network_approved:
        raise PermissionError("dependency network access requires separate approval")
    if not network_enabled and profile.lockfiles and not any((project / name).is_file() for name in profile.lockfiles):
        step = ValidationStep("dependencies", (), "failed", None,
                              stderr="offline validation requires a recognized lockfile")
        return ProjectValidationResult(profile.language, mode, "partial", False, (step,), False,
                                       trusted_tests is not None)
    trusted = trusted_tests.resolve() if trusted_tests else None
    if trusted and not trusted.is_dir():
        raise ValueError("trusted test bundle must be a directory")
    commands = canonical_commands(profile, project)
    results: list[ValidationStep] = []
    for capability, command in commands:
        if mode == "local":
            executable = command[0]
            resolved_executable = shutil.which(executable)
            if resolved_executable is None:
                results.append(ValidationStep(capability, command, "unavailable", None,
                                              stderr=f"required tool unavailable: {executable}"))
                continue
            local_command = (resolved_executable, *command[1:])
            environment = sanitized_environment(project)
            environment["PATH"] = f"{Path(resolved_executable).parent}:{environment.get('PATH', '')}"
            sandbox = _run_command(runner, local_command, project, timeout_seconds, "local", profile.language,
                                   environment=environment)
        elif mode == "container":
            if runtime not in ALLOWED_CONTAINER_RUNTIMES:
                raise ToolError(f"Unsupported container runtime: {runtime}", kind="invalid_runtime")
            if shutil.which(runtime) is None:
                results.append(ValidationStep(capability, command, "unavailable", None,
                                              stderr=f"container runtime unavailable: {runtime}"))
                break
            argv = project_container_command(project, profile, command, runtime=runtime,
                                             trusted_tests=trusted, network_enabled=network_enabled)
            sandbox = _run_command(runner, argv, project, timeout_seconds, "container", profile.language)
        else:
            raise ToolError(f"unsupported validation mode: {mode}", kind="invalid_sandbox_mode")
        results.append(_step(capability, command, sandbox))
        if sandbox.returncode != 0 or sandbox.timed_out:
            break
    unavailable = any(step.status == "unavailable" for step in results)
    tier = "unavailable" if results and all(step.status == "unavailable" for step in results) else (
        "partial" if unavailable or len(results) < len(commands) else "full"
    )
    passed = bool(results) and len(results) == len(commands) and all(step.status == "passed" for step in results)
    return ProjectValidationResult(profile.language, mode, tier, passed, tuple(results), network_enabled,
                                   trusted is not None)


def project_container_command(root: Path, profile: LanguageProfile, command: Sequence[str], *,
                              runtime: str, trusted_tests: Path | None,
                              network_enabled: bool) -> list[str]:
    allowed = ("/workspace", "/tmp", "/trusted-tests") if trusted_tests else ("/workspace", "/tmp")
    policy = ContainerFilesystemPolicy(allowed_destinations=allowed,
                                       allow_extra_bind_mounts=trusted_tests is not None)
    mounts = policy.default_mounts(root)
    if trusted_tests:
        mounts.append(ContainerMount(trusted_tests, "/trusted-tests", read_only=True))
    mounts = policy.validate_mounts(mounts)
    argv = [runtime, "run", "--rm", "--interactive", "--read-only", "--network",
            "bridge" if network_enabled else "none", "--cap-drop", "ALL", "--security-opt",
            "no-new-privileges", "--pids-limit", "64", "--memory", "512m", "--cpus", "1.0"]
    for mount in mounts:
        if mount.mount_type == "tmpfs":
            argv.extend(("--tmpfs", f"{mount.destination}:rw,noexec,nosuid,size=64m"))
        else:
            spec = f"type=bind,src={mount.source},dst={mount.destination}"
            if mount.read_only:
                spec += ",ro=true"
            argv.extend(("--mount", spec))
    argv.extend(("--workdir", "/workspace", profile.container_image, *command))
    _assert_command_obeys_policy(argv, policy)
    return argv


def _step(capability: str, command: tuple[str, ...], result: SandboxResult) -> ValidationStep:
    status = "failed" if result.timed_out or result.returncode != 0 else "passed"
    return ValidationStep(capability, command, status, result.returncode, result.stdout, result.stderr,
                          result.timed_out)


def _package_has_script(root: Path, name: str) -> bool:
    import json
    try:
        value = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(value.get("scripts"), dict) and isinstance(value["scripts"].get(name), str)
