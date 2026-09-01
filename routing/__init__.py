"""Stable, lazy entry points for bridge and repository-tool integrations.

Lazy loading keeps model backends free to import ``routing.tools`` without
creating a bridge -> backend -> routing import cycle.
"""

from typing import Any

__all__ = ["Bridge", "EventWriter", "build_default_tool_registry"]


def __getattr__(name: str) -> Any:
    if name in {"Bridge", "EventWriter"}:
        from routing.bridge import Bridge, EventWriter

        return {"Bridge": Bridge, "EventWriter": EventWriter}[name]
    if name == "build_default_tool_registry":
        from routing.tools import build_default_tool_registry

        return build_default_tool_registry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
