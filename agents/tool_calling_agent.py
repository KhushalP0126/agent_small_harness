"""Bounded model -> tool -> result loop for repository inspection tasks."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from harness_kernel.tool_handlers import (
    ApplySearchReplaceRequest,
    CreateFileRequest,
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
    "create_file",
    "execute_script",
)
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
DEFAULT_TOOL_NUM_CTX = 8192
DEFAULT_TRANSCRIPT_MAX_CHARS = DEFAULT_TOOL_NUM_CTX * 3


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
        transcript_max_chars: int = DEFAULT_TRANSCRIPT_MAX_CHARS,
        on_tool_result: ToolResultCallback | None = None,
        allowed_tools: tuple[str, ...] = TOOL_NAMES,
    ) -> None:
        self.generate_text = generate_text
        self.registry = registry
        self.max_turns = max(1, min(int(max_turns), 20))
        self.transcript_max_chars = max(256, int(transcript_max_chars))
        self.on_tool_result = on_tool_result
        self.allowed_tools = tuple(tool for tool in allowed_tools if tool in TOOL_NAMES)

    def run(self, task: str, *, max_turns_override: int | None = None) -> ToolCallingRun:
        transcript: list[dict] = []
        calls: list[ToolCallRecord] = []
        seen_calls: set[str] = set()
        turn_limit = self.max_turns if max_turns_override is None else max(1, min(int(max_turns_override), 20))
        for turn in range(1, turn_limit + 1):
            response = self.generate_text(
                _tool_prompt(
                    task,
                    transcript,
                    turn,
                    turn_limit,
                    self.transcript_max_chars,
                    self.allowed_tools,
                )
            )
            action = _parse_action(response)
            if action["action"] == "final":
                return ToolCallingRun(
                    final_answer=str(action.get("answer") or "").strip(),
                    calls=calls,
                )

            tool = str(action.get("tool") or "")
            arguments = action.get("arguments")
            if _doc_edit_is_complete(calls, tool, arguments):
                return ToolCallingRun(
                    final_answer=(
                        "The requested documentation diff is prepared for review; "
                        "no additional edit was needed."
                    ),
                    calls=calls,
                )
            raw_value = None
            signature = json.dumps(
                {"tool": tool, "arguments": arguments},
                sort_keys=True,
                default=str,
            )
            if signature in seen_calls:
                result_payload = {
                    "ok": False,
                    "error_kind": "repeated_tool_call",
                    "error": "This exact tool call was already made. Return a final answer or choose a different tool.",
                }
            elif tool not in self.allowed_tools or not isinstance(arguments, dict):
                result_payload = {
                    "ok": False,
                    "error_kind": "invalid_tool_call",
                    "error": "Use one declared tool with an object-valued arguments field.",
                }
            else:
                seen_calls.add(signature)
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


def _bounded_transcript(transcript: list[dict], max_chars: int) -> str:
    """Keep whole transcript entries so the model always receives valid JSON."""

    kept: list[dict] = []
    total = 2
    for entry in reversed(transcript):
        entry_text = json.dumps(entry, default=str, separators=(",", ":"))
        if total + len(entry_text) + 1 > max_chars and kept:
            break
        kept.append(entry)
        total += len(entry_text) + 1
    kept.reverse()
    if len(kept) < len(transcript):
        kept.insert(
            0,
            {"note": f"{len(transcript) - len(kept)} earlier turn(s) omitted for space"},
        )
    return json.dumps(kept, default=str, separators=(",", ":"))


def _doc_edit_is_complete(
    calls: list[ToolCallRecord],
    tool: str,
    arguments: Any,
) -> bool:
    """Finish a one-file doc edit after redundant same-file verification."""

    if tool not in {"read_file", "search_directory"} or not isinstance(arguments, dict):
        return False
    proposals = [
        call
        for call in calls
        if call.tool in {"apply_search_replace", "create_file"}
        and call.result.get("ok")
        and isinstance(call.result.get("value"), dict)
        and (
            call.tool == "create_file"
            or int(call.result["value"].get("replacements", 0)) > 0
        )
    ]
    if not proposals:
        return False
    proposed_path = str(proposals[-1].arguments.get("path") or "")
    requested_path = str(arguments.get("path") or arguments.get("pattern") or "")
    if tool == "search_directory":
        requested_path = str(arguments.get("pattern") or "")
    return bool(proposed_path and requested_path and proposed_path in requested_path)


def _tool_prompt(
    task: str,
    transcript: list[dict],
    turn: int,
    max_turns: int,
    transcript_max_chars: int = DEFAULT_TRANSCRIPT_MAX_CHARS,
    allowed_tools: tuple[str, ...] = TOOL_NAMES,
) -> str:
    instructions = [
        "UNIFIED CLI ASSISTANT",
        "Answer ordinary conversation directly. Use the declared repository tools only when they add useful evidence or perform a requested reviewed change.",
        f"Available tools: {', '.join(allowed_tools)}.",
        "No tool writes repository files. apply_search_replace returns an unapplied diff for human review.",
    ]
    if "execute_script" in allowed_tools:
        instructions.append(
            "execute_script runs generated source in a disposable Docker sandbox with no network by default; language defaults to Python and may be python, c, cpp, rust, or javascript."
        )
    elif "apply_search_replace" in allowed_tools or "create_file" in allowed_tools:
        instructions.append(
            "For a filesystem change, use create_file for new files and apply_search_replace for replace or delete; execute_script is unavailable."
        )
    instructions.extend(
        [
            "For a greeting, explanation, or other non-repository question, return action=final immediately without a tool call.",
            "If the latest tool result answers the task, return action=final immediately. Never repeat an identical tool call.",
            "On the final allowed turn, return action=final now; do not call another tool.",
            "Return exactly one JSON object and no markdown.",
            "To call a tool:",
        ]
    )
    if "search_directory" in allowed_tools:
        instructions.append(
            '{"action":"tool","tool":"search_directory","arguments":{"root":".","pattern":"*.py","max_results":50}}'
        )
    if "read_file" in allowed_tools:
        instructions.append(
            '{"action":"tool","tool":"read_file","arguments":{"root":".","path":"src/main.py","max_bytes":64000}}'
        )
    if "apply_search_replace" in allowed_tools:
        instructions.extend(
            [
                '{"action":"tool","tool":"apply_search_replace","arguments":{"root":".","path":"src/main.py","search":"old","replace":"new"}}',
                '{"action":"tool","tool":"apply_search_replace","arguments":{"root":".","path":"obsolete.txt","operation":"delete","search":"","replace":""}}',
            ]
        )
    if "create_file" in allowed_tools:
        instructions.append(
            '{"action":"tool","tool":"create_file","arguments":{"root":".","path":"exp/hello.txt","content":"hello world\\n"}}'
        )
    if "execute_script" in allowed_tools:
        instructions.append(
            '{"action":"tool","tool":"execute_script","arguments":{"root":".","language":"python","source":"print(1)","timeout_seconds":10}}'
        )
    instructions.extend(
        [
            "When finished:",
            '{"action":"final","answer":"concise evidence-based answer"}',
            f"Turn: {turn}/{max_turns}",
            "FINAL TURN: return action=final now." if turn == max_turns else "",
            f"TASK:\n{task.strip()}",
            "PRIOR TOOL TRANSCRIPT:",
            _bounded_transcript(transcript, transcript_max_chars),
        ]
    )
    return "\n".join(instructions)


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
            operation=str(arguments.get("operation") or "replace"),
        )
    if tool == "create_file":
        return CreateFileRequest(
            root=root,
            path=str(arguments.get("path") or ""),
            content=str(arguments.get("content") or ""),
        )
    return ExecuteScriptRequest(
        root=root,
        source=str(arguments.get("source") or ""),
        timeout_seconds=float(arguments.get("timeout_seconds") or 10.0),
        language=str(arguments.get("language") or "python"),
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
    value = _compact_tool_value(value)
    return {"ok": True, "tool": result.tool, "value": value}


def _compact_tool_value(value: Any) -> Any:
    """Keep replayable evidence small without discarding status or identity."""

    if not isinstance(value, dict):
        return value
    compact = dict(value)
    for key, limit in (("content", 4000), ("diff", 4000), ("stdout", 2000), ("stderr", 2000)):
        payload = compact.get(key)
        if isinstance(payload, str) and len(payload) > limit:
            compact[key] = payload[:limit] + f"\n[tool result compacted after {limit} characters]"
    return compact
