from __future__ import annotations

import ast
import re
import sys

from engines.base import BaseEngine, EngineDiagnostic, EngineFinding
from engines.decomposition_engine import DecompositionEngine, ImportRecord, StructuralIR
from engines.import_extractors import extract_imports
from engines.import_risk import match_call_risks, match_import_risks
from engines.library_registry import LibraryRegistry


MUTATING_METHODS = {
    "append",
    "extend",
    "insert",
    "pop",
    "remove",
    "clear",
    "sort",
    "reverse",
    "add",
    "discard",
    "update",
    "setdefault",
}

# Kept for external callers / tests that imported the previous constant.
ALLOWED_IMPORT_ROOTS = set(getattr(sys, "stdlib_module_names", set())) | {"__future__"}


def _import_root(name: str) -> str:
    return name.lstrip(".").split(".", 1)[0]


def _attribute_chain(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [*_attribute_chain(node.value), node.attr]
    return []


def _python_call_sites(source: str) -> list[tuple[str, int]]:
    tree = ast.parse(source)
    calls: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _attribute_chain(node.func)
        if chain:
            calls.append((".".join(chain), getattr(node, "lineno", 0)))
        elif isinstance(node.func, ast.Name):
            calls.append((node.func.id, getattr(node, "lineno", 0)))
    return calls


def _external_imports_from_records(
    imports: list[ImportRecord],
    registered_libraries: set[str],
) -> list[str]:
    """Policy: non-stdlib, non-registered absolute Python imports."""
    found: set[str] = set()
    for record in imports:
        if record.language != "python":
            continue
        if getattr(record, "relative_level", 0):
            continue
        if getattr(record, "is_stdlib", False):
            continue
        root = _import_root(record.name)
        if not root or root == ".":
            continue
        if root not in registered_libraries:
            found.add(root)
    return sorted(found)


def _import_bindings_from_records(
    imports: list[ImportRecord],
    registry: LibraryRegistry,
    language: str = "python",
) -> dict[str, tuple[str, str]]:
    """Map local bound name -> (library_root, call_prefix) from shared IR."""
    bindings: dict[str, tuple[str, str]] = {}
    for record in imports:
        if record.language != language or record.kind != "module":
            continue
        if getattr(record, "relative_level", 0):
            continue
        root = _import_root(record.name)
        if not root or not registry.is_registered(root, language=language):
            continue
        symbols = list(record.bound_symbols or [])
        paths = list(getattr(record, "bound_paths", None) or [])
        # Pad paths if an older ImportRecord lacked bound_paths.
        while len(paths) < len(symbols):
            paths.append("")
        for local_name, prefix in zip(symbols, paths):
            bindings[local_name] = (root, prefix)
    return bindings


def _unknown_api_calls(
    source: str,
    registry: LibraryRegistry,
    *,
    imports: list[ImportRecord],
    language: str = "python",
) -> list[dict]:
    if language != "python":
        return _non_python_unknown_api_calls(source, registry, imports=imports, language=language)
    tree = ast.parse(source)
    bindings = _import_bindings_from_records(imports, registry, language=language)
    unknown: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _attribute_chain(node.func)
        if not chain:
            continue
        binding = bindings.get(chain[0])
        if binding is None:
            continue
        library, prefix = binding
        schema = registry.get(library, language=language)
        if schema is None:
            continue
        # Registered library calls are opaque — only allow-list is checked.
        suffix = ".".join(chain[1:])
        call_path = ".".join(part for part in (prefix, suffix) if part)
        if call_path and call_path not in schema.allowed_calls:
            unknown.append(
                {
                    "library": library,
                    "call": call_path,
                    "line": getattr(node, "lineno", 0),
                    "allowed_calls": sorted(schema.allowed_calls),
                    "context": schema.context,
                    "repair": schema.unknown_api_repair,
                }
            )
    return unknown


def _non_python_unknown_api_calls(
    source: str,
    registry: LibraryRegistry,
    *,
    imports: list[ImportRecord],
    language: str,
) -> list[dict]:
    """Check qualified calls for reviewed Rust/JavaScript library surfaces.

    The registry deliberately remains conservative: only a library explicitly
    imported by the draft and explicitly approved for that language is
    inspected. Unknown packages are left to the dependency/import policies,
    rather than being mistaken for trusted APIs.
    """

    if language not in {"rust", "javascript"}:
        return []
    library_names: set[str] = set()
    for record in imports:
        if record.language != language:
            continue
        if language == "rust":
            name = record.name.split("::", 1)[0]
        else:
            name = record.name
            if name.startswith(("./", "../", "/")):
                continue
        if registry.is_registered(name, language=language):
            library_names.add(name)

    unknown: list[dict] = []
    for library in sorted(library_names):
        schema = registry.get(library, language=language)
        if schema is None:
            continue
        aliases = {library}
        if language == "javascript":
            escaped = re.escape(library)
            aliases.update(
                match.group("alias")
                for pattern in (
                    rf"\bimport\s+(?P<alias>[A-Za-z_$][\w$]*)\s+from\s+['\"]{escaped}['\"]",
                    rf"\b(?:const|let|var)\s+(?P<alias>[A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*['\"]{escaped}['\"]\s*\)",
                    rf"\bimport\s+\*\s+as\s+(?P<alias>[A-Za-z_$][\w$]*)\s+from\s+['\"]{escaped}['\"]",
                )
                for match in re.finditer(pattern, source)
            )
        for alias in aliases:
            separator = "::" if language == "rust" else "."
            pattern = rf"\b{re.escape(alias)}{re.escape(separator)}(?P<call>[A-Za-z_$][\w$]*)\s*\("
            for match in re.finditer(pattern, source):
                call = match.group("call")
                qualified = f"{library}{separator}{call}"
                if call in schema.allowed_calls or qualified in schema.allowed_calls:
                    continue
                unknown.append(
                    {
                        "library": library,
                        "call": call,
                        "line": source.count("\n", 0, match.start()) + 1,
                        "allowed_calls": sorted(schema.allowed_calls),
                        "context": schema.context,
                        "repair": schema.unknown_api_repair,
                    }
                )
    return unknown


def _unknown_api_finding(unknown_api_calls: list[dict], language: str) -> EngineFinding:
    calls = [f"{item['library']}.{item['call']}" for item in unknown_api_calls]
    repairs = [item["repair"] for item in unknown_api_calls if item["repair"]]
    return EngineFinding(
        engine=HazardsEngine.name,
        severity="High",
        summary="Unknown registered-library API usage",
        details=(
            f"Generated {language} source calls APIs that are not present in the "
            "reviewed library schema."
        ),
        metrics={
            "unknown_api_calls": calls,
            "library_context": {
                item["library"]: item["context"]
                for item in unknown_api_calls
                if item["context"]
            },
            "allowed_calls": {
                item["library"]: item["allowed_calls"]
                for item in unknown_api_calls
            },
        },
        diagnostic=EngineDiagnostic(
            violation="UNKNOWN_API",
            threshold="reviewed library schema",
            actual=", ".join(calls),
            location=", ".join(f"line {item['line']}" for item in unknown_api_calls),
            recommended_refactor=repairs[0]
            if repairs
            else "Use only APIs listed in the reviewed library schema.",
        ),
    )


def _risk_findings(
    language: str, source: str, imports: list[ImportRecord], engine_name: str
) -> list[EngineFinding]:
    hits = match_import_risks(language, imports)
    if language == "python":
        hits.extend(match_call_risks(language, _python_call_sites(source)))
    seen: set[tuple[str, str, int, str]] = set()
    unique = []
    for hit in hits:
        key = (hit.category, hit.symbol, hit.line, hit.source)
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)

    findings: list[EngineFinding] = []
    by_category: dict[str, list] = {}
    for hit in unique:
        by_category.setdefault(hit.category, []).append(hit)

    for category, category_hits in sorted(by_category.items()):
        enforcement = category_hits[0].enforcement
        symbols = sorted({hit.symbol for hit in category_hits})
        lines = sorted({hit.line for hit in category_hits if hit.line})
        severity = "High" if enforcement == "hard_block" else "Medium"
        summary = (
            f"Import risk ({category})"
            if enforcement == "hard_block"
            else f"Advisory import risk ({category})"
        )
        findings.append(
            EngineFinding(
                engine=engine_name,
                severity=severity,
                summary=summary,
                details=(
                    f"{'Hard-block' if enforcement == 'hard_block' else 'Advisory'} "
                    f"category {category} matched: {', '.join(symbols)}."
                ),
                metrics={
                    "risk_category": category,
                    "enforcement": enforcement,
                    "symbols": symbols,
                    "language": language,
                    "lines": lines,
                },
                diagnostic=EngineDiagnostic(
                    violation="IMPORT_RISK_BLOCK"
                    if enforcement == "hard_block"
                    else "IMPORT_RISK_ADVISORY",
                    threshold=enforcement,
                    actual=", ".join(symbols),
                    location=", ".join(f"line {line}" for line in lines)
                    or "import/call site",
                    recommended_refactor=(
                        f"Remove or replace {category} usage; prefer safer structured APIs."
                    ),
                ),
            )
        )
    return findings


class HazardsEngine(BaseEngine):
    name = "engine-2-hazards"

    def __init__(
        self,
        library_registry: LibraryRegistry | None = None,
        language: str = "python",
    ) -> None:
        self.library_registry = library_registry or LibraryRegistry()
        self.language = language.strip().lower()

    def scan(self, source: str, ir: StructuralIR | None = None) -> list[EngineFinding]:
        language = self.language
        if language == "python":
            ir = ir or DecompositionEngine().decompose(source)
            imports = list(ir.imports)
        else:
            # Non-Python extraction stays in import_extractors until multilang IR lands.
            imports = extract_imports(language, source)
            if ir is not None and not ir.imports:
                ir.imports = list(imports)

        findings: list[EngineFinding] = []

        if language == "python" and ir is not None:
            container_mutations = {
                mutation.target
                for mutation in ir.mutations
                if mutation.target in ir.module_state_names
                and mutation.mutation_type.startswith("call.")
                and mutation.mutation_type.split(".", 1)[1] in MUTATING_METHODS
            }
            subscript_mutations = {
                mutation.target
                for mutation in ir.mutations
                if mutation.target in ir.module_state_names
                and "subscript" in mutation.mutation_type
            }

            for global_name in ir.explicit_globals:
                findings.append(
                    EngineFinding(
                        engine=self.name,
                        severity="High",
                        summary="Global mutation hazard",
                        details="Global statements increase coupling across generated paths.",
                        metrics={"global_names": [global_name]},
                        diagnostic=EngineDiagnostic(
                            violation="GLOBAL_MUTATION",
                            actual=global_name,
                            location="global statement",
                            recommended_refactor=(
                                "Remove the global statement and pass state through explicit function arguments or return values."
                            ),
                        ),
                    )
                )

            external_imports = _external_imports_from_records(
                imports, self.library_registry.libraries(language)
            )
            if external_imports:
                findings.append(
                    EngineFinding(
                        engine=self.name,
                        severity="High",
                        summary="External dependency usage",
                        details="Generated Python source imports modules outside the standard library allowlist.",
                        metrics={"imports": external_imports},
                        diagnostic=EngineDiagnostic(
                            violation="EXTERNAL_DEPENDENCY",
                            threshold="standard library only",
                            actual=", ".join(external_imports),
                            location="import statement",
                            recommended_refactor=(
                                "Remove third-party imports and implement the required behavior with standard-library modules or local helpers."
                            ),
                        ),
                    )
                )

            if container_mutations:
                findings.append(
                    EngineFinding(
                        engine=self.name,
                        severity="High",
                        summary="Module-level container mutation hazard",
                        details="Module-scope containers are being mutated through method calls.",
                        metrics={"container_names": sorted(container_mutations)},
                        diagnostic=EngineDiagnostic(
                            violation="MODULE_STATE_MUTATION",
                            actual=", ".join(sorted(container_mutations)),
                            location="module-level container method call",
                            recommended_refactor=(
                                "Move mutable state into local scope, pass it as an explicit argument, or return a new value instead of mutating module state."
                            ),
                        ),
                    )
                )
            if subscript_mutations:
                findings.append(
                    EngineFinding(
                        engine=self.name,
                        severity="High",
                        summary="Module-level subscript mutation hazard",
                        details="Module-scope containers are being mutated through indexed assignment.",
                        metrics={"container_names": sorted(subscript_mutations)},
                        diagnostic=EngineDiagnostic(
                            violation="MODULE_STATE_MUTATION",
                            actual=", ".join(sorted(subscript_mutations)),
                            location="module-level subscript assignment",
                            recommended_refactor=(
                                "Replace indexed mutation of module state with local data construction and explicit return values."
                            ),
                        ),
                    )
                )

        unknown_api_calls = _unknown_api_calls(
            source,
            self.library_registry,
            imports=imports,
            language=language,
        )
        if unknown_api_calls:
            findings.append(_unknown_api_finding(unknown_api_calls, language))

        findings.extend(_risk_findings(language, source, imports, self.name))

        if findings:
            return findings
        return [
            EngineFinding(
                engine=self.name,
                severity="Low",
                summary="No global mutation hazard detected",
                details="Generated source does not declare global variables.",
                metrics={"module_state_names": ir.module_state_names if ir else []},
            )
        ]
