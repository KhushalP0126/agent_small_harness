"""Container and explicit local execution for generated source artifacts."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from harness_kernel.language_adapters import get_language_adapter
from harness_kernel.local_sandbox import MAX_CAPTURE_BYTES, sanitized_environment
from harness_kernel.tool_registry import ToolError


CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]

# Host-enforced filesystem policy for container-only execution.
# Only the scratch workspace is bind-mounted; the container root is read-only
# with a noexec tmpfs on /tmp. Network defaults to disabled ("none").
# Local fallback is never implicit (allow_local_fallback must be explicit).
HOST_FS_ALLOWLIST = ("/workspace", "/tmp")
CONTAINER_DEFAULT_NETWORK = "none"
CONTAINER_WORKDIR = "/workspace"
CONTAINER_TMPFS_PATH = "/tmp"
ALLOWED_CONTAINER_RUNTIMES = frozenset({"docker", "podman"})
ALLOWED_SANDBOX_MODES = frozenset({"container", "local"})


@dataclass(frozen=True)
class ContainerMount:
    """A single host->container mount requested for sandbox execution."""

    source: Path
    destination: str
    read_only: bool = False
    mount_type: str = "bind"  # bind | tmpfs


@dataclass(frozen=True)
class ContainerFilesystemPolicy:
    """Host-enforced mount/path policy for container-only tool execution."""

    allowed_destinations: tuple[str, ...] = HOST_FS_ALLOWLIST
    workdir: str = CONTAINER_WORKDIR
    tmpfs_destination: str = CONTAINER_TMPFS_PATH
    allow_extra_bind_mounts: bool = False
    require_read_only_root: bool = True
    require_no_new_privileges: bool = True
    default_network: str = CONTAINER_DEFAULT_NETWORK

    def normalize_destination(self, destination: str) -> str:
        dest = destination.strip()
        if not dest.startswith("/"):
            raise ToolError(
                f"Container mount destination must be absolute: {destination!r}",
                kind="fs_policy_violation",
            )
        # Collapse redundant slashes / dots without resolving host symlinks.
        normalized = Path(dest).as_posix()
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        if normalized != "/" and normalized.endswith("/"):
            normalized = normalized.rstrip("/")
        return normalized or "/"

    def is_allowed_destination(self, destination: str) -> bool:
        dest = self.normalize_destination(destination)
        allowed = {self.normalize_destination(item) for item in self.allowed_destinations}
        if dest in allowed:
            return True
        # Permit nested paths only under explicitly allowed prefixes.
        return any(dest.startswith(prefix.rstrip("/") + "/") for prefix in allowed)

    def validate_host_source(self, source: Path, *, label: str = "mount source") -> Path:
        try:
            resolved = source.expanduser().resolve(strict=False)
        except OSError as exc:
            raise ToolError(
                f"Invalid {label}: {source}",
                kind="fs_policy_violation",
            ) from exc
        if not resolved.is_absolute():
            raise ToolError(
                f"{label} must be an absolute path: {source}",
                kind="fs_policy_violation",
            )
        # Reject obviously dangerous host roots even if destination is allowlisted.
        blocked_roots = {"/", "/etc", "/home", "/Users", "/var/run/docker.sock", "/private"}
        posix = resolved.as_posix()
        if posix in blocked_roots:
            raise ToolError(
                f"{label} is a blocked host path: {posix}",
                kind="fs_policy_violation",
            )
        if posix.endswith("/docker.sock") or posix == "/var/run/docker.sock":
            raise ToolError(
                "Mounting the Docker socket is forbidden by container FS policy",
                kind="fs_policy_violation",
            )
        return resolved

    def validate_mounts(self, mounts: Iterable[ContainerMount]) -> list[ContainerMount]:
        validated: list[ContainerMount] = []
        seen_destinations: set[str] = set()
        for mount in mounts:
            dest = self.normalize_destination(mount.destination)
            if dest in seen_destinations:
                raise ToolError(
                    f"Duplicate container mount destination: {dest}",
                    kind="fs_policy_violation",
                )
            seen_destinations.add(dest)
            if not self.is_allowed_destination(dest):
                raise ToolError(
                    f"Container mount destination not allowlisted: {dest}",
                    kind="fs_policy_violation",
                )
            if mount.mount_type == "bind":
                source = self.validate_host_source(mount.source)
                validated.append(
                    ContainerMount(
                        source=source,
                        destination=dest,
                        read_only=mount.read_only,
                        mount_type="bind",
                    )
                )
            elif mount.mount_type == "tmpfs":
                if dest != self.normalize_destination(self.tmpfs_destination):
                    # tmpfs only permitted on the designated temp path by default.
                    if not self.allow_extra_bind_mounts:
                        raise ToolError(
                            f"tmpfs destination not permitted: {dest}",
                            kind="fs_policy_violation",
                        )
                validated.append(
                    ContainerMount(
                        source=Path("."),  # unused for tmpfs
                        destination=dest,
                        read_only=False,
                        mount_type="tmpfs",
                    )
                )
            else:
                raise ToolError(
                    f"Unsupported mount type: {mount.mount_type}",
                    kind="fs_policy_violation",
                )

        # Required mounts for container-only tool policy.
        required = {
            self.normalize_destination(self.workdir),
            self.normalize_destination(self.tmpfs_destination),
        }
        if not required.issubset(seen_destinations):
            missing = sorted(required - seen_destinations)
            raise ToolError(
                f"Container FS policy missing required mounts: {', '.join(missing)}",
                kind="fs_policy_violation",
            )
        if not self.allow_extra_bind_mounts:
            extras = sorted(
                dest
                for dest in seen_destinations
                if dest not in required
            )
            if extras:
                raise ToolError(
                    f"Extra container mounts are disabled by policy: {', '.join(extras)}",
                    kind="fs_policy_violation",
                )
        return validated

    def default_mounts(self, scratch_dir: Path) -> list[ContainerMount]:
        scratch = self.validate_host_source(scratch_dir, label="scratch directory")
        return [
            ContainerMount(
                source=scratch,
                destination=self.workdir,
                read_only=False,
                mount_type="bind",
            ),
            ContainerMount(
                source=Path("."),
                destination=self.tmpfs_destination,
                read_only=False,
                mount_type="tmpfs",
            ),
        ]


DEFAULT_CONTAINER_FS_POLICY = ContainerFilesystemPolicy()


@dataclass(frozen=True)
class SandboxResult:
    language: str
    mode: str
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool


def enforce_container_only_tool_policy(
    *,
    mode: str,
    allow_local_sandbox: bool = False,
    allow_local_fallback: bool = False,
) -> str:
    """Reject local execution unless the host explicitly enables it."""

    normalized = mode.strip().lower()
    if normalized not in ALLOWED_SANDBOX_MODES:
        raise ToolError(f"Unsupported sandbox mode: {mode}", kind="invalid_sandbox_mode")
    if normalized == "local" and not allow_local_sandbox:
        raise ToolError(
            "Local script execution is disabled for registered tools; use the container policy",
            kind="local_sandbox_disabled",
        )
    if normalized == "container" and allow_local_fallback and not allow_local_sandbox:
        # Fallback is a local execution path; require the same host approval bit.
        raise ToolError(
            "Local fallback requires explicit allow_local_sandbox approval",
            kind="local_sandbox_disabled",
        )
    return normalized


def container_command(
    scratch_dir: Path,
    language: str,
    *,
    runtime: str = "docker",
    network_enabled: bool = False,
    extra_mounts: Sequence[ContainerMount] | None = None,
    policy: ContainerFilesystemPolicy | None = None,
) -> list[str]:
    """Build a hardened container run command under the host FS allowlist policy."""

    adapter = get_language_adapter(language)
    if runtime not in ALLOWED_CONTAINER_RUNTIMES:
        raise ToolError(f"Unsupported container runtime: {runtime}", kind="invalid_runtime")

    fs_policy = policy or DEFAULT_CONTAINER_FS_POLICY
    mounts = list(fs_policy.default_mounts(scratch_dir))
    if extra_mounts:
        if not fs_policy.allow_extra_bind_mounts:
            raise ToolError(
                "Extra container mounts are disabled by host FS policy",
                kind="fs_policy_violation",
            )
        mounts.extend(extra_mounts)
    validated_mounts = fs_policy.validate_mounts(mounts)

    network_mode = "bridge" if network_enabled else fs_policy.default_network
    command: list[str] = [
        runtime,
        "run",
        "--rm",
        "--interactive",
    ]
    if fs_policy.require_read_only_root:
        command.append("--read-only")
    command.extend(
        [
            "--network",
            network_mode,
            "--cap-drop",
            "ALL",
        ]
    )
    if fs_policy.require_no_new_privileges:
        command.extend(["--security-opt", "no-new-privileges"])
    command.extend(
        [
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--cpus",
            "1.0",
        ]
    )

    for mount in validated_mounts:
        if mount.mount_type == "tmpfs":
            # noexec/nosuid tmpfs on the allowlisted temp path only.
            command.extend(
                [
                    "--tmpfs",
                    f"{mount.destination}:rw,noexec,nosuid,size=64m",
                ]
            )
        else:
            options = ["type=bind", f"src={mount.source}", f"dst={mount.destination}"]
            if mount.read_only:
                options.append("ro=true")
            command.extend(["--mount", ",".join(options)])

    command.extend(
        [
            "--workdir",
            fs_policy.workdir,
            adapter.container_image,
            *adapter.container_command,
        ]
    )
    _assert_command_obeys_policy(command, fs_policy)
    return command


def _assert_command_obeys_policy(command: Sequence[str], policy: ContainerFilesystemPolicy) -> None:
    """Final host-side scan of the argv that will be executed."""

    text_parts = list(command)
    if policy.require_read_only_root and "--read-only" not in text_parts:
        raise ToolError("Container command missing --read-only rootfs", kind="fs_policy_violation")
    if policy.require_no_new_privileges and "no-new-privileges" not in text_parts:
        raise ToolError(
            "Container command missing no-new-privileges",
            kind="fs_policy_violation",
        )

    # Reject docker.sock or arbitrary -v / --volume host mounts.
    joined = " ".join(text_parts)
    if "docker.sock" in joined:
        raise ToolError(
            "Docker socket mounts are forbidden by container FS policy",
            kind="fs_policy_violation",
        )
    if "-v" in text_parts or "--volume" in text_parts:
        raise ToolError(
            "Raw -v/--volume mounts are forbidden; use policy-validated --mount entries",
            kind="fs_policy_violation",
        )

    # Every --mount dst= must be allowlisted.
    for index, part in enumerate(text_parts):
        if part != "--mount":
            continue
        if index + 1 >= len(text_parts):
            raise ToolError("Container command has dangling --mount", kind="fs_policy_violation")
        spec = text_parts[index + 1]
        destination = None
        for field in spec.split(","):
            if field.startswith("dst="):
                destination = field[4:]
                break
        if destination is None:
            raise ToolError(
                f"Container mount missing dst=: {spec}",
                kind="fs_policy_violation",
            )
        if not policy.is_allowed_destination(destination):
            raise ToolError(
                f"Container mount destination not allowlisted: {destination}",
                kind="fs_policy_violation",
            )

    # tmpfs destinations must also be allowlisted.
    for index, part in enumerate(text_parts):
        if part != "--tmpfs":
            continue
        if index + 1 >= len(text_parts):
            raise ToolError("Container command has dangling --tmpfs", kind="fs_policy_violation")
        dest = text_parts[index + 1].split(":", 1)[0]
        if not policy.is_allowed_destination(dest):
            raise ToolError(
                f"tmpfs destination not allowlisted: {dest}",
                kind="fs_policy_violation",
            )


def run_source_isolated(
    source: str,
    language: str,
    *,
    timeout_seconds: float = 10.0,
    mode: str = "container",
    runtime: str = "docker",
    network_enabled: bool = False,
    allow_local_fallback: bool = False,
    enforce_container_only_tools: bool = False,
    allow_local_sandbox: bool = False,
    extra_mounts: Sequence[ContainerMount] | None = None,
    policy: ContainerFilesystemPolicy | None = None,
    runner: CommandRunner = subprocess.run,
) -> SandboxResult:
    """Execute source with no implicit fallback across a security boundary.

    When ``enforce_container_only_tools`` is true (tool-registry path), local
    mode and local fallback require explicit ``allow_local_sandbox``. Direct
    callers may still choose ``mode="local"`` without that flag.
    """

    adapter = get_language_adapter(language)
    normalized_mode = mode.strip().lower()
    if normalized_mode not in ALLOWED_SANDBOX_MODES:
        raise ToolError(f"Unsupported sandbox mode: {mode}", kind="invalid_sandbox_mode")
    if enforce_container_only_tools:
        enforce_container_only_tool_policy(
            mode=normalized_mode,
            allow_local_sandbox=allow_local_sandbox,
            allow_local_fallback=allow_local_fallback,
        )

    fs_policy = policy or DEFAULT_CONTAINER_FS_POLICY
    with tempfile.TemporaryDirectory(prefix="agent-harness-exec-") as temp_dir:
        scratch = Path(temp_dir)
        (scratch / adapter.filename).write_text(source, encoding="utf-8")
        if normalized_mode == "container":
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
                extra_mounts=extra_mounts,
                policy=fs_policy,
            )
            return _run_command(
                runner,
                command,
                scratch,
                timeout_seconds,
                "container",
                adapter.language,
            )
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
