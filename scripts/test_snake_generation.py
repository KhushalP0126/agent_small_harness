from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.engine_registry import EngineRegistry
from agents.parse_contract import ParseContractAgent, ParseSuccess
from agents.template_loader import TemplateLibrary
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaClient, OllamaGenerationConfig, OllamaModelSupplier
from validation.policy import validate_findings


REQUESTS = [
    (
        "python",
        "py",
        "Write a complete, single-file terminal Snake game in Python using the standard "
        "curses module. Include the game loop, food spawning, growth, and collision/game-over. "
        "Return only runnable Python code with no explanation and no markdown fences.",
    ),
    (
        "cpp",
        "cpp",
        "Write a complete, single-file terminal Snake game in C++ (C++17) using only the "
        "standard library and ncurses. Include the game loop, food, growth, and collisions. "
        "Return only compilable C++ code with no explanation and no markdown fences.",
    ),
    (
        "c",
        "c",
        "Write a complete, single-file terminal Snake game in C (C11) using ncurses. Include "
        "the game loop, food, growth, and collisions. Return only compilable C code with no "
        "explanation and no markdown fences.",
    ),
]


def _prompt_with_skeleton(language: str, prompt: str) -> str:
    skeleton = TemplateLibrary().load("snake", language)
    if not skeleton:
        return prompt
    return (
        f"{prompt}\n\n"
        "Use this parseable skeleton as the base structure. Fill the TODOs and keep the "
        "same language. Return only the completed source code.\n\n"
        f"{skeleton}"
    )


def _analyze(language: str, code: str) -> dict:
    """Run a generated draft through the harness gate and engines."""
    parse_contract = ParseContractAgent()
    registry = EngineRegistry.default()
    parse_result = parse_contract.parse(code, language=language)
    if not isinstance(parse_result, ParseSuccess):
        return {
            "gate": "ParseFailure",
            "language": parse_result.language,
            "reason": parse_result.finding.summary,
            "verdict": "manual_review_required",
        }
    findings = registry.findings_for(code, parse_result.language)
    validation = validate_findings(findings, policy={"max_cyclomatic_complexity": 7})
    return {
        "gate": "ParseSuccess",
        "language": parse_result.language,
        "findings": [f"{f.engine}: {f.summary}" for f in findings],
        "violations": [f"{v.kind} ({v.current_value} -> {v.allowed_value})" for v in validation.violations],
        "verdict": "completed" if validation.is_compliant else "manual_review_required",
    }


def run(model: str, num_predict: int, timeout_seconds: int) -> None:
    supplier = OllamaModelSupplier(
        client=OllamaClient(timeout_seconds=timeout_seconds),
        model=model,
        config=OllamaGenerationConfig(num_predict=num_predict, num_ctx=4096),
    )
    out_dir = Path(tempfile.mkdtemp(prefix="snake_test_"))
    print(f"Model: {model}")
    print(f"Output dir: {out_dir}\n")

    for language, ext, prompt in REQUESTS:
        print(f"=== {language.upper()} ===")
        try:
            code = supplier.generate_draft(_prompt_with_skeleton(language, prompt))
        except Exception as exc:  # noqa: BLE001 - smoke test should report, not crash
            print(f"  generation error: {exc.__class__.__name__}: {exc}\n")
            continue
        path = out_dir / f"snake.{ext}"
        path.write_text(code + "\n", encoding="utf-8")
        report = _analyze(language, code)
        print(f"  saved: {path} ({len(code)} chars)")
        print(f"  gate: {report['gate']} (language={report['language']})")
        if report["gate"] == "ParseSuccess":
            for finding in report["findings"]:
                print(f"    finding: {finding}")
            if report["violations"]:
                for violation in report["violations"]:
                    print(f"    violation: {violation}")
            else:
                print("    violations: none")
        else:
            print(f"    reason: {report['reason']}")
        print(f"  harness verdict: {report['verdict']}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the model to write Snake in python/cpp/c and run it through the harness.")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--num-predict", type=int, default=2048)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    run(model=args.model, num_predict=args.num_predict, timeout_seconds=args.timeout)


if __name__ == "__main__":
    main()
