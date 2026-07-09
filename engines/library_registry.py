from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIBRARY_REGISTRY = ROOT / "data" / "library_registry.json"


@dataclass(frozen=True)
class LibrarySchema:
    name: str
    allowed_calls: frozenset[str] = field(default_factory=frozenset)
    allowed_constants: frozenset[str] = field(default_factory=frozenset)
    context: str = ""
    unknown_api_repair: str = ""


class LibraryRegistry:
    def __init__(self, path: Path | str = DEFAULT_LIBRARY_REGISTRY) -> None:
        self.path = Path(path)
        self._schemas = self._load()

    def _load(self) -> dict[str, LibrarySchema]:
        if not self.path.is_file():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        schemas = {}
        for name, payload in raw.get("libraries", {}).items():
            normalized = name.strip().lower()
            schemas[normalized] = LibrarySchema(
                name=normalized,
                allowed_calls=frozenset(payload.get("allowed_calls", [])),
                allowed_constants=frozenset(payload.get("allowed_constants", [])),
                context=payload.get("context", ""),
                unknown_api_repair=payload.get("unknown_api_repair", ""),
            )
        return schemas

    def libraries(self) -> set[str]:
        return set(self._schemas)

    def get(self, name: str) -> LibrarySchema | None:
        return self._schemas.get(name.strip().lower())

    def is_registered(self, name: str) -> bool:
        return name.strip().lower() in self._schemas
