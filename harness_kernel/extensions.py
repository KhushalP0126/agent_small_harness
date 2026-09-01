"""Manifest-driven, bounded subprocess extensions."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Mapping

from harness_kernel.governance import PermissionEvaluator
from harness_kernel.tool_registry import ToolHandler, ToolRegistry


@dataclass(frozen=True)
class ExtensionRequest:
    payload: dict[str, Any]
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtensionResponse:
    ok: bool
    result: dict[str, Any]


@dataclass(frozen=True)
class ExtensionManifest:
    name: str
    version: str
    command: str
    arguments: tuple[str, ...] = ()
    capabilities: frozenset[str] = field(default_factory=frozenset)
    lifecycle_hooks: frozenset[str] = field(default_factory=frozenset)
    timeout_seconds: float = 10.0
    maximum_output: int = 1_000_000
    environment_allowlist: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExtensionManifest":
        required = ("name", "version", "command")
        if any(not isinstance(value.get(key), str) or not value[key] for key in required):
            raise ValueError("extension manifest requires non-empty name, version, and command")
        timeout = float(value.get("timeout", 10.0))
        maximum = int(value.get("maximum_output", 1_000_000))
        if timeout <= 0 or maximum <= 0:
            raise ValueError("extension limits must be positive")
        return cls(
            name=value["name"], version=value["version"], command=value["command"],
            arguments=tuple(map(str, value.get("arguments", ()))),
            capabilities=frozenset(map(str, value.get("capabilities", ()))),
            lifecycle_hooks=frozenset(map(str, value.get("lifecycle_hooks", ()))),
            timeout_seconds=min(timeout, 300.0), maximum_output=min(maximum, 10_000_000),
            environment_allowlist=frozenset(map(str, value.get("environment_allowlist", ()))),
        )


class ExtensionAdapter:
    def __init__(self, permissions: PermissionEvaluator) -> None:
        self.permissions = permissions

    def invoke(self, manifest: ExtensionManifest, request: Mapping[str, Any]) -> dict[str, Any]:
        requested = set(map(str, request.get("capabilities", ())))
        denied = requested - manifest.capabilities
        if denied:
            return {"ok": False, "kind": "capability_denied", "capabilities": sorted(denied)}
        for capability in requested:
            decision = self.permissions.evaluate(capability, f"extension:{manifest.name}")
            if not decision.allowed:
                return {"ok": False, "kind": "approval_required" if decision.approval_required else "permission_denied", "reason": decision.reason}
        environment = {key: os.environ[key] for key in manifest.environment_allowlist if key in os.environ}
        with tempfile.TemporaryFile() as output_file:
            process = subprocess.Popen(
                [manifest.command, *manifest.arguments], stdin=subprocess.PIPE,
                stdout=output_file, stderr=subprocess.DEVNULL, env=environment,
            )
            try:
                process.communicate(
                    json.dumps(dict(request)).encode("utf-8"), timeout=manifest.timeout_seconds
                )
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return {"ok": False, "kind": "timeout", "timeout_seconds": manifest.timeout_seconds}
            output_size = output_file.tell()
            if output_size > manifest.maximum_output:
                return {"ok": False, "kind": "output_limit_exceeded"}
            output_file.seek(0)
            output = output_file.read(manifest.maximum_output).decode("utf-8", errors="replace")
        try:
            response = json.loads(output)
        except json.JSONDecodeError:
            return {"ok": False, "kind": "malformed_response", "exit_code": process.returncode}
        if not isinstance(response, dict):
            return {"ok": False, "kind": "malformed_response", "exit_code": process.returncode}
        return {"ok": process.returncode == 0, "exit_code": process.returncode, "response": response}


def register_extension_tools(
    registry: ToolRegistry,
    manifests: list[ExtensionManifest],
    permissions: PermissionEvaluator,
) -> None:
    adapter = ExtensionAdapter(permissions)
    for manifest in manifests:
        def invoke(request: ExtensionRequest, selected: ExtensionManifest = manifest) -> ExtensionResponse:
            result = adapter.invoke(
                selected,
                {"payload": request.payload, "capabilities": list(request.capabilities)},
            )
            return ExtensionResponse(bool(result.get("ok")), result)
        registry.register(ToolHandler(
            name=f"extension.{manifest.name}", request_type=ExtensionRequest,
            response_type=ExtensionResponse, invoke=invoke,
            description=f"Extension {manifest.name} {manifest.version}",
        ))
