from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from typing import Any

from agents.base import AgentResult, BaseAgent
from engines.base import EngineFinding


PARSE_CONTRACT_ENGINE = "engine-parse-contract"
SUPPORTED_LANGUAGES = {"python"}
TREE_SITTER_LANGUAGES = {"c", "cpp", "rust", "javascript"}

PYTHON_EXTENSIONS = (".py", ".pyi")
C_EXTENSIONS = (".c", ".h")
CPP_EXTENSIONS = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")
RUST_EXTENSIONS = (".rs",)
JAVASCRIPT_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx")

LANGUAGE_ALIASES = {
    "py": "python",
    "python3": "python",
    "c++": "cpp",
    "cxx": "cpp",
    "rs": "rust",
    "js": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
}


@dataclass
class ParseSuccess:
    language: str
    tree: Any = None


@dataclass
class ParseFailure:
    language: str
    error: str
    finding: EngineFinding


def detect_language(source: str, language: str | None = None, filename: str | None = None) -> str:
    """Resolve the language of a draft.

    Explicit hints win (caller-provided language, then filename extension); otherwise
    fall back to a lightweight content sniff. Defaults to ``python`` so existing
    Python-only flows are unchanged.
    """
    if language:
        normalized = language.strip().lower()
        return LANGUAGE_ALIASES.get(normalized, normalized)
    if filename:
        lowered = filename.lower()
        if lowered.endswith(PYTHON_EXTENSIONS):
            return "python"
        if lowered.endswith(CPP_EXTENSIONS):
            return "cpp"
        if lowered.endswith(C_EXTENSIONS):
            return "c"
        if lowered.endswith(RUST_EXTENSIONS):
            return "rust"
        if lowered.endswith(JAVASCRIPT_EXTENSIONS):
            return "javascript"
    return _sniff_language(source)

CPP_SIGNALS = (
    "std::",
    "#include <iostream>",
    "#include <vector>",
    "template<",
    "template <",
    "using namespace",
    "::",
)

RUST_SIGNALS = (
    "fn ",
    "let mut ",
    "impl ",
    "pub ",
    "use ",
    "::",
)

JAVASCRIPT_SIGNALS = (
    "function ",
    "const ",
    "let ",
    "=>",
    "require(",
    "module.exports",
)


def _sniff_language(source: str) -> str:
    """Infer a supported language from distinctive syntax, without parsing it.

    The signals intentionally favour Rust/JavaScript markers before the looser
    C++ ``::`` marker.  Explicit language and filename hints still win.
    """
    c_signals = 0
    if "#include" in source:
        c_signals += 2
    if "int main" in source or "void " in source:
        c_signals += 1

    cpp_signals = sum(1 for token in CPP_SIGNALS if token in source)
    rust_keywords = {"fn ", "let mut ", "impl ", "pub ", "use "}
    rust_signals = sum(
        2 if token in rust_keywords else 1
        for token in RUST_SIGNALS
        if token in source
    )
    javascript_signals = sum(
        2 if token in {"function ", "const ", "=>", "require(", "module.exports"} else 1
        for token in JAVASCRIPT_SIGNALS
        if token in source
    )

    python_signals = 0
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("def ", "class ", "import ", "from ", "@")):
            python_signals += 2
        if stripped.endswith(":"):
            python_signals += 1
    # Rust source commonly uses ``::`` too; it must be considered before C++.
    if rust_signals > max(cpp_signals, c_signals, python_signals, javascript_signals):
        return "rust"
    if javascript_signals > max(cpp_signals, c_signals, python_signals, rust_signals):
        return "javascript"
    if cpp_signals and (c_signals or cpp_signals) >= max(python_signals, rust_signals, javascript_signals):
        return "cpp"

    if c_signals > max(python_signals, javascript_signals):
        return "c"
    if rust_signals > python_signals:
        return "rust"
    if javascript_signals > python_signals:
        return "javascript"
    return "python"


class ParseContractAgent(BaseAgent):
    """Typed parser gate.

    Detects the language, attempts to parse, and returns either a ``ParseSuccess``
    (carrying the parsed tree for supported languages) or a ``ParseFailure`` with a
    standard ``engine-parse-contract`` finding. This prevents downstream engines from
    ever touching raw unsupported syntax and keeps the controller from hallucinating
    structure for code it cannot actually parse.
    """

    name = "agent-parse-contract"

    def __init__(self, supported_languages: set[str] | None = None) -> None:
        self.supported_languages = supported_languages or set(SUPPORTED_LANGUAGES)

    def parse(
        self,
        source: str,
        language: str | None = None,
        filename: str | None = None,
    ) -> ParseSuccess | ParseFailure:
        detected = detect_language(source, language=language, filename=filename)
        if detected == "python":
            return self._parse_python(source, detected)
        if detected in TREE_SITTER_LANGUAGES:
            return self._parse_tree_sitter(source, detected)
        return self._unsupported(detected)

    def _parse_python(self, source: str, language: str) -> ParseSuccess | ParseFailure:
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return ParseFailure(
                language=language,
                error=exc.msg or "syntax error",
                finding=EngineFinding(
                    engine=PARSE_CONTRACT_ENGINE,
                    severity="High",
                    summary="Draft parse failure",
                    details=f"Generated draft is not valid Python: {exc.msg}",
                    metrics={
                        "line": exc.lineno or 0,
                        "offset": exc.offset or 0,
                        "error": exc.msg,
                    },
                ),
            )
        return ParseSuccess(language=language, tree=tree)

    def _parse_tree_sitter(self, source: str, language: str) -> ParseSuccess | ParseFailure:
        try:
            from engines import treesitter_support

            if not treesitter_support.is_language_available(language):
                return self._unsupported(language)
            tree = treesitter_support.parse_tree(language, source)
            root = tree.root_node
        except Exception:
            return self._unsupported(language)

        if root.has_error:
            error_node = treesitter_support.first_error_node(root) or root
            line, column = error_node.start_point
            return ParseFailure(
                language=language,
                error="tree-sitter parse error",
                finding=EngineFinding(
                    engine=PARSE_CONTRACT_ENGINE,
                    severity="High",
                    summary="Draft parse failure",
                    details=f"Generated draft is not valid {language}: tree-sitter parse error",
                    metrics={
                        "line": line + 1,
                        "offset": column + 1,
                        "error": "tree-sitter parse error",
                        "language": language,
                    },
                ),
            )
        return ParseSuccess(language=language, tree=tree)

    def _unsupported(self, language: str) -> ParseFailure:
        return ParseFailure(
            language=language,
            error=f"unsupported language: {language}",
            finding=EngineFinding(
                engine=PARSE_CONTRACT_ENGINE,
                severity="High",
                summary="Unsupported language",
                details=(
                    f"No parser or engine set is registered for language '{language}'. "
                    "Refusing to run Python engines against non-Python syntax."
                ),
                metrics={
                    "line": 0,
                    "offset": 0,
                    "error": "unsupported_language",
                    "language": language,
                },
            ),
        )

    def run(
        self,
        source: str,
        language: str | None = None,
        filename: str | None = None,
    ) -> AgentResult:
        result = self.parse(source, language=language, filename=filename)
        if isinstance(result, ParseSuccess):
            return AgentResult(
                agent=self.name,
                payload={"status": "success", "language": result.language},
            )
        return AgentResult(
            agent=self.name,
            payload={
                "status": "failure",
                "language": result.language,
                "error": result.error,
                "finding": asdict(result.finding),
            },
        )
