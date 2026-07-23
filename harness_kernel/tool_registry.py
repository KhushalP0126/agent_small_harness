"""Typed tool-handler dispatch with uniform failure handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


class ToolError(Exception):
    """Expected tool failure carrying a stable machine-readable kind."""

    def __init__(self, message: str, kind: str = "tool_error") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class ToolResult(Generic[ResponseT]):
    """Uniform outcome of a dispatched tool call."""

    ok: bool
    tool: str = ""
    value: ResponseT | None = None
    error: str | None = None
    error_kind: str | None = None

    @classmethod
    def success(cls, tool: str, value: ResponseT) -> "ToolResult[ResponseT]":
        return cls(ok=True, tool=tool, value=value)

    @classmethod
    def failure(
        cls,
        tool: str,
        error: str,
        error_kind: str = "tool_error",
    ) -> "ToolResult[Any]":
        return cls(ok=False, tool=tool, error=error, error_kind=error_kind)


@dataclass(frozen=True)
class ToolHandler(Generic[RequestT, ResponseT]):
    """Named action with declared request and response types."""

    name: str
    request_type: type
    response_type: type
    invoke: Callable[[RequestT], ResponseT]
    description: str = ""


class ToolRegistry:
    """Look up typed handlers and contain all dispatch failures."""

    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, handler: ToolHandler) -> None:
        if handler.name in self._handlers:
            raise ValueError(f"Tool '{handler.name}' is already registered")
        self._handlers[handler.name] = handler

    def unregister(self, name: str) -> None:
        self._handlers.pop(name, None)

    def get(self, name: str) -> ToolHandler | None:
        return self._handlers.get(name)

    def known_tools(self) -> list[str]:
        return sorted(self._handlers)

    def dispatch(self, name: str, request: Any) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult.failure(
                tool=name,
                error=f"No tool registered under '{name}'. Known tools: {self.known_tools()}",
                error_kind="unknown_tool",
            )
        if handler.request_type is not type(None) and not isinstance(
            request, handler.request_type
        ):
            return ToolResult.failure(
                tool=name,
                error=(
                    f"Tool '{name}' expected a {handler.request_type.__name__} request, "
                    f"got {type(request).__name__}"
                ),
                error_kind="invalid_request_type",
            )
        try:
            response = handler.invoke(request)
        except ToolError as exc:
            return ToolResult.failure(tool=name, error=str(exc), error_kind=exc.kind)
        except Exception as exc:  # noqa: BLE001 - dispatch is the failure boundary
            return ToolResult.failure(
                tool=name,
                error=f"{type(exc).__name__}: {exc}",
                error_kind="handler_exception",
            )
        if handler.response_type is not type(None) and not isinstance(
            response, handler.response_type
        ):
            return ToolResult.failure(
                tool=name,
                error=(
                    f"Tool '{name}' returned a {type(response).__name__}, "
                    f"expected {handler.response_type.__name__}"
                ),
                error_kind="invalid_response_type",
            )
        return ToolResult.success(tool=name, value=response)
