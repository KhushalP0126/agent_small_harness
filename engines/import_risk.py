"""Language-agnostic import/call risk category matching."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from engines.decomposition_engine import ImportRecord

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMPORT_RISK_PATH = ROOT / "data" / "import_risk_categories.json"

HARD_BLOCK_CATEGORIES = frozenset({"process_exec", "unsafe_memory", "dynamic_eval"})
ADVISORY_CATEGORIES = frozenset({"raw_filesystem", "network"})


@dataclass(frozen=True)
class RiskHit:
    category: str
    enforcement: str
    symbol: str
    line: int
    source: str  # import | call | construct


@dataclass
class CategoryRule:
    category: str
    enforcement: str
    imports: set[str] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)
    constructs: set[str] = field(default_factory=set)


@lru_cache(maxsize=4)
def load_risk_rules(path: str | None = None) -> dict[str, dict[str, CategoryRule]]:
    """Return mapping language -> category -> CategoryRule."""
    risk_path = Path(path) if path else DEFAULT_IMPORT_RISK_PATH
    if not risk_path.is_file():
        return {}
    raw = json.loads(risk_path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, CategoryRule]] = {}
    for category, payload in raw.get("categories", {}).items():
        enforcement = payload.get("enforcement") or (
            "hard_block" if category in HARD_BLOCK_CATEGORIES else "advisory"
        )
        for key, value in payload.items():
            if key == "enforcement" or not isinstance(value, dict):
                continue
            language = key.strip().lower()
            rule = CategoryRule(
                category=category,
                enforcement=enforcement,
                imports={item.strip() for item in value.get("imports", []) if item},
                calls={item.strip() for item in value.get("calls", []) if item},
                constructs={item.strip() for item in value.get("constructs", []) if item},
            )
            out.setdefault(language, {})[category] = rule
    return out


def enforcement_for(category: str, language: str = "python") -> str:
    rules = load_risk_rules().get(language.strip().lower(), {})
    rule = rules.get(category)
    if rule is not None:
        return rule.enforcement
    if category in HARD_BLOCK_CATEGORIES:
        return "hard_block"
    if category in ADVISORY_CATEGORIES:
        return "advisory"
    return "advisory"


def _import_name_matches(import_name: str, patterns: set[str]) -> bool:
    name = import_name.strip()
    if name in patterns:
        return True
    # Match package roots and nested paths (e.g. std::process::Command vs std::process).
    for pattern in patterns:
        if name == pattern or name.startswith(pattern + ".") or name.startswith(pattern + "::"):
            return True
        if pattern.startswith(name + ".") or pattern.startswith(name + "::"):
            return True
    return False


def match_import_risks(
    language: str,
    imports: Iterable[ImportRecord],
    *,
    path: str | None = None,
) -> list[RiskHit]:
    language = language.strip().lower()
    rules = load_risk_rules(path).get(language, {})
    hits: list[RiskHit] = []
    for record in imports:
        for rule in rules.values():
            if _import_name_matches(record.name, rule.imports):
                hits.append(
                    RiskHit(
                        category=rule.category,
                        enforcement=rule.enforcement,
                        symbol=record.name,
                        line=record.line,
                        source="import",
                    )
                )
    return hits


def match_call_risks(
    language: str,
    calls: Iterable[tuple[str, int]],
    *,
    path: str | None = None,
) -> list[RiskHit]:
    language = language.strip().lower()
    rules = load_risk_rules(path).get(language, {})
    hits: list[RiskHit] = []
    for call_name, line in calls:
        normalized = call_name.strip()
        bare = normalized.split(".")[-1].split("::")[-1]
        for rule in rules.values():
            if normalized in rule.calls or bare in rule.calls:
                hits.append(
                    RiskHit(
                        category=rule.category,
                        enforcement=rule.enforcement,
                        symbol=normalized,
                        line=line,
                        source="call",
                    )
                )
                continue
            # Prefix match for qualified forms listed as package.api
            for pattern in rule.calls:
                if normalized == pattern or normalized.endswith("." + pattern) or normalized.endswith("::" + pattern):
                    hits.append(
                        RiskHit(
                            category=rule.category,
                            enforcement=rule.enforcement,
                            symbol=normalized,
                            line=line,
                            source="call",
                        )
                    )
                    break
    return hits


def match_construct_risks(
    language: str,
    constructs: Iterable[tuple[str, int]],
    *,
    path: str | None = None,
) -> list[RiskHit]:
    language = language.strip().lower()
    rules = load_risk_rules(path).get(language, {})
    hits: list[RiskHit] = []
    for name, line in constructs:
        for rule in rules.values():
            if name in rule.constructs:
                hits.append(
                    RiskHit(
                        category=rule.category,
                        enforcement=rule.enforcement,
                        symbol=name,
                        line=line,
                        source="construct",
                    )
                )
    return hits
