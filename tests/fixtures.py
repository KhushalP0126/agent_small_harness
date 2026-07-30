"""Shared test fixtures for isolating the historian's shared state.

Constructing a :class:`~agents.generation_controller.GenerationController`
without an explicit ``historian=`` falls back to
``HistorianAgent(DEFAULT_HISTORY_PATH)`` -- the real, git-tracked
``history.json`` at the repository root. Under ``python -m unittest discover``
every test module shares one process, so that single shared file makes
otherwise-deterministic controller tests depend on whatever ran before them:
``GenerationController.run`` reads it through ``_with_historical_context`` and,
with the default echo ``draft_supplier``, can fold advisory history text into
the draft that then gets scanned.

These helpers point the default at a throwaway, empty history file instead, so
the real ``history.json`` is never read or written by the suite. The
constructor already accepts ``historian: HistorianAgent | None = None`` as the
supported extension point, so this is a test-fixture concern, not a production
change.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Callable

from agents.historian import HistorianAgent

EMPTY_HISTORY = {"schema_version": 1, "lessons_learned": [], "generations": []}


def seed_empty_history(path: Path) -> Path:
    """Write an empty-but-valid history document to ``path`` and return it."""

    path.write_text(json.dumps(EMPTY_HISTORY) + "\n", encoding="utf-8")
    return path


def make_test_historian(directory: Path | str) -> HistorianAgent:
    """Return a ``HistorianAgent`` backed by an isolated, empty history file.

    ``directory`` should be a per-test temporary directory (for example the
    path yielded by ``tempfile.TemporaryDirectory``) so no two tests share
    history state.
    """

    return HistorianAgent(seed_empty_history(Path(directory) / "history.json"))


def install_default_history_isolation() -> Callable[[], None]:
    """Redirect the default history path to a temp file for the whole session.

    Both ``agents.historian`` and ``agents.generation_controller`` bind
    ``DEFAULT_HISTORY_PATH`` in their own module namespaces, so both are
    repointed. Returns a cleanup callable that restores the originals and
    removes the temporary directory.
    """

    import agents.generation_controller as controller_module
    import agents.historian as historian_module

    tmp = tempfile.TemporaryDirectory(prefix="agent-coder-history-")
    path = seed_empty_history(Path(tmp.name) / "history.json")
    original_historian = historian_module.DEFAULT_HISTORY_PATH
    original_controller = controller_module.DEFAULT_HISTORY_PATH
    historian_module.DEFAULT_HISTORY_PATH = path
    controller_module.DEFAULT_HISTORY_PATH = path

    def cleanup() -> None:
        historian_module.DEFAULT_HISTORY_PATH = original_historian
        controller_module.DEFAULT_HISTORY_PATH = original_controller
        tmp.cleanup()

    return cleanup
