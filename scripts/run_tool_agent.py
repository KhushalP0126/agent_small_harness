from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.config_loader import DEFAULT_CONFIG_PATH, load_config
from agents.tool_calling_agent import ToolCallingAgent
from backends.architect_client import ArchitectApiClient, ArchitectConfig
from backends.ollama_client import (
    DEFAULT_OLLAMA_MODEL,
    OllamaClient,
    OllamaGenerationConfig,
)
from harness_kernel.tool_handlers import build_default_tool_registry


def _generator(provider: str, model: str, config_path: Path):
    if provider == "qwen":
        client = OllamaClient()
        selected_model = model or DEFAULT_OLLAMA_MODEL

        def generate(prompt: str) -> str:
            return client.generate(
                prompt,
                model=selected_model,
                config=OllamaGenerationConfig(
                    temperature=0.0,
                    num_predict=1200,
                    num_ctx=8192,
                ),
                system="Return one repository tool-call JSON object only.",
            )

        return generate

    harness_config = load_config(config_path)
    architect_config = ArchitectConfig(
        repair_profile=harness_config.execution.architect.repair
    )
    client = ArchitectApiClient(architect_config)
    profile = architect_config.repair_profile_from_env
    if model and model != profile.model:
        profile = type(profile)(
            model=model,
            timeout_seconds=profile.timeout_seconds,
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
            thinking_type=profile.thinking_type,
            reasoning_effort=profile.reasoning_effort,
        )

    def generate(prompt: str) -> str:
        return client.generate(
            prompt,
            system="Return one repository tool-call JSON object only.",
            profile=profile,
        )

    return generate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a bounded repository tool-calling agent without applying diffs."
    )
    parser.add_argument("task", help="Repository inspection or diff-preparation task")
    parser.add_argument("--provider", choices=("qwen", "deepseek"), default="qwen")
    parser.add_argument("--model", default="")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    repository_root = args.repo_root.resolve()
    run = ToolCallingAgent(
        _generator(args.provider, args.model, args.config),
        build_default_tool_registry(repository_root=repository_root),
        max_turns=args.max_turns,
    ).run(args.task)
    print(json.dumps(asdict(run), indent=2, default=str))
    return 1 if run.exhausted else 0


if __name__ == "__main__":
    raise SystemExit(main())
