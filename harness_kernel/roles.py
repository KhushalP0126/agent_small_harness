"""Closed registry of typed orchestration roles."""
from __future__ import annotations

from dataclasses import dataclass

from harness_kernel.task_graph import LANGUAGES, Role


@dataclass(frozen=True)
class RoleManifest:
    name: str
    version: int
    allowed_tools: tuple[str, ...]
    capabilities: frozenset[str]
    languages: frozenset[str]
    providers: frozenset[str]
    prompt_template: str
    timeout_seconds: int = 900
    max_turns: int = 16
    max_output_bytes: int = 1_000_000
    may_edit: bool = False


class RoleRegistry:
    def __init__(self, manifests: tuple[RoleManifest, ...] | None = None) -> None:
        values = manifests or BUILTIN_ROLES
        self._roles = {manifest.name: manifest for manifest in values}
        if len(self._roles) != len(values):
            raise ValueError("role names must be unique")

    def get(self, name: str) -> RoleManifest:
        try:
            return self._roles[name]
        except KeyError as exc:
            raise ValueError(f"unregistered role: {name}") from exc

    def authorize(self, role: str, capability: str, *, language: str, provider: str) -> None:
        manifest = self.get(role)
        if capability not in manifest.capabilities:
            raise PermissionError(f"role {role} cannot use capability {capability}")
        if language not in manifest.languages or provider not in manifest.providers:
            raise PermissionError(f"role {role} does not allow {language}/{provider}")


_READ = frozenset({"read"})
_EDIT = frozenset({"read", "write", "command"})
_PROVIDERS = frozenset({"qwen", "api"})
BUILTIN_ROLES = tuple(
    RoleManifest(role.value, 1, ("repository.read",), _READ, LANGUAGES, _PROVIDERS,
                 f"roles/{role.value}.txt")
    for role in (Role.PLANNER, Role.RESEARCHER, Role.VALIDATOR)
) + tuple(
    RoleManifest(role.value, 1, ("repository.read", "workspace.edit", "sandbox.command"), _EDIT,
                 LANGUAGES, _PROVIDERS, f"roles/{role.value}.txt", may_edit=True)
    for role in (Role.IMPLEMENTER, Role.CONFLICT_REPAIR)
)
