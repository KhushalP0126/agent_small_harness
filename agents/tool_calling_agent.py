"""Bounded model -> tool -> result loop for repository inspection tasks."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from harness_kernel.tool_handlers import (
    ApplySearchReplaceRequest,
    ExecuteScriptRequest,
    ReadFileRequest,
    SearchDirectoryRequest,
)
from harness_kernel.tool_registry import ToolRegistry


GenerateText = Callable[[str], str]
ToolResultCallback = Callable[["ToolCallRecord", Any | None], None]
TOOL_NAMES = (
    "search_directory",
    "read_file",
    "apply_search_replace",
    "execute_script",
)
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ToolCallRecord:
    turn: int
    tool: str
    arguments: dict
    result: dict


@dataclass(frozen=True)
class ToolCallingRun:
    final_answer: str
    calls: list[ToolCallRecord]
    exhausted: bool = False


class ToolCallingAgent:
    """Let a text model select typed tools while retaining a hard turn bound."""

    def __init__(
        self,
        generate_text: GenerateText,
        registry: ToolRegistry,
        *,
        max_turns: int = 8,
        on_tool_result: ToolResultCallback | None = None,
    ) -> None:
        self.generate_text = generate_text
        self.registry = registry
        self.max_turns = max(1, min(int(max_turns), 20))
        self.on_tool_result = on_tool_result

    def run(self, task: str) -> ToolCallingRun:
        transcript: list[dict] = []
        calls: list[ToolCallRecord] = []
        for turn in range(1, self.max_turns + 1):
            response = self.generate_text(_tool_prompt(task, transcript, turn, self.max_turns))
            action = _parse_action(response)
            if action["action"] == "final":
                return ToolCallingRun(
                    final_answer=str(action.get("answer") or "").strip(),
                    calls=calls,
                )

            tool = str(action.get("tool") or "")
            arguments = action.get("arguments")
            raw_value = None
            if tool not in TOOL_NAMES or not isinstance(arguments, dict):
                result_payload = {
                    "ok": False,
                    "error_kind": "invalid_tool_call",
                    "error": "Use one declared tool with an object-valued arguments field.",
                }
            else:
                try:
                    request = _request_from_arguments(tool, arguments)
                except (TypeError, ValueError) as exc:
                    result_payload = {
                        "ok": False,
                        "tool": tool,
                        "error_kind": "invalid_arguments",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                else:
                    dispatched = self.registry.dispatch(tool, request)
                    result_payload = _result_payload(dispatched)
                    raw_value = dispatched.value if dispatched.ok else None
            record = ToolCallRecord(turn, tool, arguments if isinstance(arguments, dict) else {}, result_payload)
            calls.append(record)
            if self.on_tool_result is not None:
                self.on_tool_result(record, raw_value)
            transcript.append(
                {
                    "assistant": action,
                    "tool_result": result_payload,
                }
            )
        return ToolCallingRun(
            final_answer="Tool-call turn limit reached before a final answer.",
            calls=calls,
            exhausted=True,
        )


def _tool_prompt(task: str, transcript: list[dict], turn: int, max_turns: int) -> str:
    return "\n".join(
        [
            "REPOSITORY TOOL-CALLING MODE",
            "Inspect and reason about the repository using only the declared tools.",
            "No tool writes repository files. apply_search_replace returns an unapplied diff for human review.",
            "execute_script runs Python in a disposable sanitized working directory, not in the repository.",
            "Return exactly one JSON object and no markdown.",
            "To call a tool:",
            '{"action":"tool","tool":"search_directory","arguments":{"root":".","pattern":"*.py","max_results":50}}',
            '{"action":"tool","tool":"read_file","arguments":{"root":".","path":"src/main.py","max_bytes":64000}}',
            '{"action":"tool","tool":"apply_search_replace","arguments":{"root":".","path":"src/main.py","search":"old","replace":"new"}}',
            '{"action":"tool","tool":"execute_script","arguments":{"root":".","source":"print(1)","timeout_seconds":10}}',
            "When finished:",
            '{"action":"final","answer":"concise evidence-based answer"}',
            f"Turn: {turn}/{max_turns}",
            f"TASK:\n{task.strip()}",
            "PRIOR TOOL TRANSCRIPT:",
            json.dumps(transcript, default=str, separators=(",", ":"))[-48_000:] or "[]",
        ]
    )


def _parse_action(response: str) -> dict:
    text = response.strip()
    match = JSON_FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()
    start = text.find("{")
    if start > 0:
        text = text[start:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"action": "invalid", "answer": ""}
    return payload if isinstance(payload, dict) else {"action": "invalid", "answer": ""}


def _request_from_arguments(tool: str, arguments: dict):
    root = Path(str(arguments.get("root") or "."))
    if tool == "search_directory":
        return SearchDirectoryRequest(
            root=root,
            pattern=str(arguments.get("pattern") or "*"),
            max_results=int(arguments.get("max_results") or 50),
        )
    if tool == "read_file":
        return ReadFileRequest(
            root=root,
            path=str(arguments.get("path") or ""),
            max_bytes=int(arguments.get("max_bytes") or 64_000),
        )
    if tool == "apply_search_replace":
        return ApplySearchReplaceRequest(
            root=root,
            path=str(arguments.get("path") or ""),
            search=str(arguments.get("search") or ""),
            replace=str(arguments.get("replace") or ""),
        )
    return ExecuteScriptRequest(
        root=root,
        source=str(arguments.get("source") or ""),
        timeout_seconds=float(arguments.get("timeout_seconds") or 10.0),
    )


def _result_payload(result) -> dict:
    if not result.ok:
        return {
            "ok": False,
            "tool": result.tool,
            "error_kind": result.error_kind,
            "error": result.error,
        }
    value = asdict(result.value) if hasattr(result.value, "__dataclass_fields__") else result.value
    if isinstance(value, dict) and "proposed_content" in value:
        value = {**value, "proposed_content": "[held for approval; use the diff for review]"}
    return {"ok": True, "tool": result.tool, "value": value}
