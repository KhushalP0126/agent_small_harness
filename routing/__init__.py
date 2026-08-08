"""Stable entry points for terminal, API, and repository-tool routing."""

from routing.bridge import Bridge, EventWriter
from routing.tools import build_default_tool_registry

__all__ = ["Bridge", "EventWriter", "build_default_tool_registry"]
