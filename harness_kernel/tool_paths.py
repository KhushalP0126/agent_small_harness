"""Repository-scoped path validation shared by model-callable tools."""

from __future__ import annotations

from pathlib import Path

from harness_kernel.tool_registry import ToolError


def resolve_within_root(root: Path, requested: str | Path) -> Path:
    """Resolve a requested path and reject traversal or symlink escapes."""

    trusted_root = root.resolve()
    candidate_input = Path(requested)
    candidate = (
        candidate_input.resolve()
        if candidate_input.is_absolute()
        else (trusted_root / candidate_input).resolve()
    )
    try:
        candidate.relative_to(trusted_root)
    except ValueError as exc:
        raise ToolError(
            f"Path {str(requested)!r} escapes repository root {str(trusted_root)!r}",
            kind="path_escape",
        ) from exc
    return candidate


def repository_relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
