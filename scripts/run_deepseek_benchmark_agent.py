"""JSONL adapter for the paired benchmark using the configured DeepSeek client."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.tool_calling_agent import ToolCallingAgent
from backends.architect_client import ArchitectApiClient, ArchitectConfig, ArchitectProfile
from harness_kernel.tool_handlers import build_default_tool_registry


def _profile(config: ArchitectConfig, max_tokens: int) -> ArchitectProfile:
    base = config.repair_profile_from_env
    return ArchitectProfile(
        model=base.model,
        timeout_seconds=max(30, base.timeout_seconds),
        temperature=0.0,
        max_tokens=max_tokens,
        thinking_type=base.thinking_type,
        reasoning_effort=base.reasoning_effort,
    )


def run_baseline(task: dict, client: ArchitectApiClient, profile: ArchitectProfile) -> dict:
    prompt = (
        "Complete this repository coding-agent benchmark task directly. "
        "Return a concise evidence-based answer describing the result.\n\n"
        f"Task: {task['prompt']}"
    )
    try:
        answer = client.generate(
            prompt,
            system="You are the baseline coding agent. Do not call tools.",
            profile=profile,
        )
        usage = client.last_usage
        return {
            "success": bool(answer.strip()),
            "prompt_tokens": usage.prompt_tokens if usage else 0,
            "completion_tokens": usage.completion_tokens if usage else 0,
            "tool_calls": 0,
            "retries": 0,
            "error": "",
        }
    except Exception as exc:  # benchmark must record failures, not abort the pair
        return {
            "success": False,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "tool_calls": 0,
            "retries": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_shielded(task: dict, client: ArchitectApiClient, profile: ArchitectProfile) -> dict:
    usage_totals = [0, 0]
    failures = [0]

    def generate(prompt: str) -> str:
        response = client.generate(
            prompt,
            system="Return exactly one JSON object for the typed repository tool loop.",
            profile=profile,
        )
        usage = client.last_usage
        if usage:
            usage_totals[0] += usage.prompt_tokens
            usage_totals[1] += usage.completion_tokens
        return response

    def observe(_record, result) -> None:
        if isinstance(result, dict) and not result.get("ok", True):
            failures[0] += 1

    try:
        run = ToolCallingAgent(
            generate,
            build_default_tool_registry(repository_root=ROOT),
            max_turns=8,
            on_tool_result=observe,
        ).run(task["prompt"])
        return {
            "success": bool(run.final_answer.strip()) and not run.exhausted,
            "prompt_tokens": usage_totals[0],
            "completion_tokens": usage_totals[1],
            "tool_calls": len(run.calls),
            "retries": failures[0],
            "error": "turn_limit" if run.exhausted else "",
        }
    except Exception as exc:  # benchmark must record failures, not abort the pair
        return {
            "success": False,
            "prompt_tokens": usage_totals[0],
            "completion_tokens": usage_totals[1],
            "tool_calls": 0,
            "retries": failures[0],
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("baseline", "shielded"), required=True)
    parser.add_argument("--max-tokens", type=int, default=1800)
    args = parser.parse_args()
    task = json.loads(sys.stdin.read())
    config = ArchitectConfig()
    if not config.api_key_configured:
        print(json.dumps({"success": False, "error": "DeepSeek API key is not configured"}))
        return 1
    profile = _profile(config, max(256, args.max_tokens))
    client = ArchitectApiClient(config)
    result = (
        run_baseline(task, client, profile)
        if args.mode == "baseline"
        else run_shielded(task, client, profile)
    )
    print(json.dumps(result))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
