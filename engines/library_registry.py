from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIBRARY_REGISTRY = ROOT / "data" / "library_registry.json"


@dataclass(frozen=True)
class LibrarySchema:
    name: str
    language: str = "python"
    allowed_calls: frozenset[str] = field(default_factory=frozenset)
    allowed_constants: frozenset[str] = field(default_factory=frozenset)
    context: str = ""
    unknown_api_repair: str = ""


def _schema_from_payload(name: str, language: str, payload: dict) -> LibrarySchema:
    return LibrarySchema(
        name=name.strip().lower(),
        language=language.strip().lower(),
        allowed_calls=frozenset(payload.get("allowed_calls", [])),
        allowed_constants=frozenset(payload.get("allowed_constants", [])),
        context=payload.get("context", ""),
        unknown_api_repair=payload.get("unknown_api_repair", ""),
    )


class LibraryRegistry:
    """Allow-list registry keyed by (language, library_name).

    JSON may be nested ``libraries[language][name]`` or legacy flat
    ``libraries[name]`` (treated as python).
    """

    def __init__(self, path: Path | str = DEFAULT_LIBRARY_REGISTRY) -> None:
        self.path = Path(path)
        self._schemas = self._load()

    def _load(self) -> dict[tuple[str, str], LibrarySchema]:
        if not self.path.is_file():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        libraries = raw.get("libraries", {})
        schemas: dict[tuple[str, str], LibrarySchema] = {}
        if not isinstance(libraries, dict):
            return {}

        for key, value in libraries.items():
            if not isinstance(value, dict):
                continue
            if _looks_like_language_bucket(value):
                language = key.strip().lower()
                for lib_name, payload in value.items():
                    if not isinstance(payload, dict):
                        continue
                    normalized = lib_name.strip().lower()
                    schemas[(language, normalized)] = _schema_from_payload(
                        normalized, language, payload
                    )
            else:
                normalized = key.strip().lower()
                schemas[("python", normalized)] = _schema_from_payload(
                    normalized, "python", value
                )
        return schemas

    def libraries(self, language: str | None = "python") -> set[str]:
        if language is None:
            return {name for (_, name) in self._schemas}
        language = language.strip().lower()
        return {name for (lang, name) in self._schemas if lang == language}

    def get(self, name: str, language: str = "python") -> LibrarySchema | None:
        return self._schemas.get((language.strip().lower(), name.strip().lower()))

    def is_registered(self, name: str, language: str = "python") -> bool:
        return (language.strip().lower(), name.strip().lower()) in self._schemas


def _looks_like_language_bucket(value: dict) -> bool:
    """True if `value` is {lib_name: schema_dict, ...} rather than a schema."""
    if not value:
        return True
    # Flat schema values are lists/strings; nested language buckets have dict values.
    return all(isinstance(item, dict) for item in value.values()) and not any(
        isinstance(item, list) for item in value.values()
    )
