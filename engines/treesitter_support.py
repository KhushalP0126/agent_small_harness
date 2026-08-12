from __future__ import annotations

import importlib
from typing import Any


# Languages handled by tree-sitter (Python stays on the stdlib `ast` engines).
_LANGUAGE_MODULES = {
    "c": "tree_sitter_c",
    "cpp": "tree_sitter_cpp",
    "rust": "tree_sitter_rust",
    "javascript": "tree_sitter_javascript",
}

# Core structural engines historically require C/C++ grammars.
_CORE_LANGUAGES = ("c", "cpp")

_available: bool | None = None
_language_available: dict[str, bool] = {}
_parsers: dict[str, Any] = {}
_tree_cache: dict[tuple[str, str], Any] = {}
_TREE_CACHE_LIMIT = 64


def supported_languages() -> tuple[str, ...]:
    return tuple(lang for lang in _LANGUAGE_MODULES if is_language_available(lang))


def is_language_available(language: str) -> bool:
    language = language.strip().lower()
    if language not in _LANGUAGE_MODULES:
        return False
    if language in _language_available:
        return _language_available[language]
    try:
        importlib.import_module("tree_sitter")
        importlib.import_module(_LANGUAGE_MODULES[language])
        _language_available[language] = True
    except Exception:
        _language_available[language] = False
    return _language_available[language]


def is_available() -> bool:
    """Return True only if tree-sitter core and both C/C++ grammars import cleanly."""
    global _available
    if _available is None:
        _available = all(is_language_available(lang) for lang in _CORE_LANGUAGES)
    return _available


def get_parser(language: str) -> Any:
    language = language.strip().lower()
    if language not in _LANGUAGE_MODULES:
        raise ValueError(f"Unsupported tree-sitter language: {language}")
    if not is_language_available(language):
        raise ValueError(f"Tree-sitter grammar unavailable for language: {language}")
    if language not in _parsers:
        from tree_sitter import Language, Parser

        grammar = importlib.import_module(_LANGUAGE_MODULES[language])
        ts_language = Language(grammar.language())
        # Parser construction changed across tree-sitter releases; handle all forms.
        try:
            parser = Parser(ts_language)
        except TypeError:
            parser = Parser()
            try:
                parser.language = ts_language
            except (AttributeError, TypeError):
                parser.set_language(ts_language)
        _parsers[language] = parser
    return _parsers[language]


def parse_tree(language: str, source: str) -> Any:
    """Parse source into a tree-sitter tree, caching by (language, source)."""
    key = (language.strip().lower(), source)
    if key not in _tree_cache:
        if len(_tree_cache) >= _TREE_CACHE_LIMIT:
            _tree_cache.clear()
        parser = get_parser(language)
        _tree_cache[key] = parser.parse(source.encode("utf-8"))
    return _tree_cache[key]


def first_error_node(root: Any) -> Any | None:
    """Breadth-first search for the first ERROR or MISSING node, if any."""
    queue = [root]
    while queue:
        node = queue.pop(0)
        if node.type == "ERROR" or getattr(node, "is_missing", False):
            return node
        queue.extend(node.children)
    return None
