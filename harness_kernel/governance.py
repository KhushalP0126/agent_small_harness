"""Central permission evaluation shared by built-ins and external tools."""
from dataclasses import dataclass
from enum import Enum


class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    DONT_ASK = "dont_ask"


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    approval_required: bool
    reason: str


class PermissionEvaluator:
    def __init__(self, mode: PermissionMode = PermissionMode.DEFAULT, allowlist: set[str] | None = None):
        self.mode = mode
        self.allowlist = allowlist or set()

    def evaluate(self, capability: str, action: str) -> PermissionDecision:
        mutating = capability in {"write", "command", "network", "destructive", "rewind"}
        if self.mode is PermissionMode.PLAN and mutating:
            return PermissionDecision(False, False, "plan mode is read-only")
        if self.mode is PermissionMode.DONT_ASK:
            allowed = action in self.allowlist
            return PermissionDecision(allowed, False, "allowlisted" if allowed else "not allowlisted")
        if self.mode is PermissionMode.ACCEPT_EDITS and capability == "write":
            return PermissionDecision(True, False, "edit preparation accepted by mode")
        if mutating:
            return PermissionDecision(False, True, f"{capability} requires approval")
        return PermissionDecision(True, False, "read-only action")
