"""Stable, secret-free provenance for research artifacts and benchmark reports."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import os
import platform
import subprocess
from typing import Any, Mapping


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of a versioned corpus or fixture file."""

    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_provenance(
    *,
    repository_root: Path,
    task_corpus: Path | None = None,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect reproducibility metadata without reading or exposing secrets."""

    root = repository_root.resolve()
    payload: dict[str, Any] = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": {
            "commit": _git(root, "rev-parse", "HEAD"),
            "dirty": bool(_git(root, "status", "--porcelain")),
        },
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
    }
    if task_corpus is not None:
        corpus = task_corpus.resolve()
        payload["task_corpus"] = {
            "path": _display_path(corpus, root),
            "sha256": file_sha256(corpus),
        }
    if settings:
        payload["settings"] = {str(key): value for key, value in settings.items()}
    return payload


def configured_model_settings() -> dict[str, Any]:
    """Return non-secret model settings available to generic artifact writers."""

    return {
        "local_model": os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:1.5b"),
        "local_context_window": _positive_int(os.environ.get("LOCAL_CONTEXT_WINDOW"), 8192),
        "architect_model": os.environ.get("ARCHITECT_MODEL", "deepseek-v4-pro"),
        "architect_context_window": _positive_int(os.environ.get("ARCHITECT_CONTEXT_WINDOW"), 65536),
        "architect_thinking_type": os.environ.get("ARCHITECT_THINKING_TYPE", "enabled"),
        "architect_reasoning_effort": os.environ.get("ARCHITECT_REASONING_EFFORT", "high"),
    }


def _git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _positive_int(value: str | None, fallback: int) -> int:
    try:
        parsed = int(value or fallback)
    except ValueError:
        return fallback
    return parsed if parsed > 0 else fallback
