"""Decode one protocol command and call the matching bridge capability."""

from __future__ import annotations

import threading
from typing import Any, Protocol


class CommandTarget(Protocol):
    """Methods and state used by command dispatch, documented in one place."""

    tool_repository_root: Any
    writer: Any
    _provider_settings: Any
    _cost_guard: Any
    _api_overage_approved: bool


def dispatch_command(bridge: CommandTarget, command: dict[str, Any]) -> None:
    kind = str(command.get("cmd") or "")
    if kind == "run":
        bridge.start_run(
            str(command.get("entrypoint") or ""),
            [str(item) for item in command.get("args") or []],
        )
    elif kind in {"prompt", "chat"}:
        bridge.start_chat(str(command.get("text") or ""))
    elif kind == "draft_spec":
        bridge.start_spec_draft()
    elif kind == "draft_research":
        bridge.start_research()
    elif kind == "questionnaire_complete":
        bridge.complete_questionnaire(command.get("answers"))
    elif kind == "execute_spec":
        bridge.start_spec_execution(str(command.get("text") or ""))
    elif kind == "tool_task":
        bridge.start_tool_task(
            str(command.get("text") or ""),
            str(command.get("provider") or "auto"),
        )
    elif kind == "apply_tool_diff":
        bridge.resolve_tool_diff(bool(command.get("approved")))
    elif kind == "approve_action":
        bridge.resolve_action_approval(bool(command.get("approved")))
    elif kind == "check":
        bridge.check_code(str(command.get("path") or ""))
    elif kind == "cancel":
        bridge.cancel()
    elif kind == "repo_map":
        bridge.repo_map(
            str(command.get("root") or bridge.tool_repository_root),
            str(command.get("focus") or ""),
            str(command.get("mode") or "diagram"),
        )
    elif kind == "compile":
        bridge.compile_source(
            str(command.get("language") or ""),
            str(command.get("source") or ""),
        )
    elif kind == "profile_samples":
        bridge.profile_samples(
            str(command.get("loop_order") or ""),
            command.get("samples_ns"),
            command.get("cache_misses"),
        )
    elif kind == "compute_shield":
        bridge.compute_shield(command.get("phase"), command.get("tasks"))
    elif kind == "history":
        bridge.history(command.get("run_id"), command.get("limit"))
    elif kind == "research_readiness":
        bridge.research_readiness()
    elif kind == "repair_session_action":
        bridge.repair_session_action(command)
    elif kind == "orchestrate":
        bridge.orchestrate(str(command.get("goal") or ""))
    elif kind == "approve_graph":
        bridge.approve_graph(
            str(command.get("session_id") or ""),
            str(command.get("revision_hash") or ""),
        )
    elif kind == "orchestration_action":
        bridge.orchestration_action(command)
    elif kind == "inspect_orchestration":
        bridge.inspect_orchestration(str(command.get("session_id") or ""))
    elif kind == "replay_orchestration":
        bridge.replay_orchestration(str(command.get("session_id") or ""))
    elif kind == "open_settings":
        bridge.emit_settings_state()
    elif kind == "save_provider_settings":
        bridge.save_provider_settings(command)
    elif kind == "test_provider_connection":
        threading.Thread(target=bridge.test_provider_connection, daemon=True).start()
    elif kind == "clear_provider_credential":
        bridge.clear_provider_credential(
            str(command.get("provider") or bridge._provider_settings.provider.value)
        )
    elif kind == "set_contribution_split":
        bridge.set_contribution_split(command)
    elif kind == "cost_cap_approval":
        bridge._api_overage_approved = bool(command.get("approved"))
        bridge.writer.emit(
            "cost_cap_approval",
            approved=bridge._api_overage_approved,
            remaining_api_budget=bridge._cost_guard.remaining_usd,
        )
    elif kind == "set_permission_mode":
        bridge.set_permission_mode(str(command.get("mode") or "default"))
    elif kind == "clear_context":
        bridge.clear_context()
    elif kind == "compact_context":
        bridge.compact_context(str(command.get("instructions") or ""))
    elif kind == "context_status":
        bridge.emit_context_state()
    elif kind == "list_checkpoints":
        bridge.emit_checkpoint_list()
    elif kind == "rewind":
        bridge.rewind(str(command.get("checkpoint_id") or ""), str(command.get("scope") or "both"))
    elif kind == "branch_checkpoint":
        bridge.branch_checkpoint(str(command.get("checkpoint_id") or ""))
    elif kind in {"extensions_status", "mcp_status"}:
        bridge.emit_extensions_state()
    else:
        bridge.writer.emit("log", level="error", msg=f"unknown command: {kind}")
