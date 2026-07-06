from __future__ import annotations

import ast
import importlib.util
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agents.base import AgentResult, BaseAgent


@dataclass
class LibraryDiscovery:
    library: str
    available: bool
    origin: str = ""
    public_symbols: list[str] = field(default_factory=list)
    proposal: dict[str, Any] = field(default_factory=dict)


class LibraryDiscoveryAgent(BaseAgent):
    """Discovers importable library surface without importing the library."""

    name = "agent-library-discovery"

    def discover(self, library: str) -> LibraryDiscovery:
        spec = importlib.util.find_spec(library)
        if spec is None or spec.origin is None:
            return LibraryDiscovery(library=library, available=False)
        origin = spec.origin
        symbols = self._public_symbols(Path(origin)) if origin.endswith(".py") else []
        return LibraryDiscovery(
            library=library,
            available=True,
            origin=origin,
            public_symbols=symbols,
            proposal=self.build_proposal(library, symbols),
        )

    def _public_symbols(self, origin: Path) -> list[str]:
        try:
            tree = ast.parse(origin.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            return []
        symbols: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                symbols.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        symbols.add(target.id)
        return sorted(symbols)

    def build_proposal(self, library: str, symbols: list[str]) -> dict[str, Any]:
        allowed_calls = sorted({symbol for symbol in symbols if symbol and not symbol.startswith("_")})
        return {
            "schema_version": "1.0",
            "library": library,
            "proposal_status": "candidate",
            "allowed_calls": allowed_calls,
            "context": (
                f"Discovered public top-level symbols for `{library}`. Review before approving into the trusted registry."
            ),
            "unknown_api_repair": (
                f"Use a reviewed `{library}` API from the trusted registry or approve a discovered candidate first."
            ),
        }

    def write_proposal(self, library: str, proposal_dir: Path) -> Path:
        discovery = self.discover(library)
        proposal_dir.mkdir(parents=True, exist_ok=True)
        path = proposal_dir / f"{library}.json"
        path.write_text(json.dumps(asdict(discovery), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def run(self, library: str) -> AgentResult:
        return AgentResult(agent=self.name, payload=asdict(self.discover(library)))
