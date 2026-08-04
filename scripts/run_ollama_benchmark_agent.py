"""JSONL adapter for paired benchmarks against a local Ollama model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.tool_calling_agent import ToolCallingAgent
from backends.ollama_client import OllamaClient, OllamaGenerationConfig
from harness_kernel.tool_handlers import build_default_tool_registry


def _usage(client: OllamaClient) -> dict[str, int]:
    usage = client.last_usage
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }


def _run(task: dict, mode: str, model: str, base_url: str, max_turns: int) -> dict:
    client = OllamaClient(base_url=base_url, timeout_seconds=180)
    if mode == "baseline":
        prompt = (
            "Complete this repository coding-agent benchmark task directly without tools. "
            "Return a concise evidence-based answer or proposed diff.\n\n"
            f"Task: {task['prompt']}"
        )
        try:
            answer = client.generate(
                prompt,
                model=model,
                config=OllamaGenerationConfig(temperature=0.0, num_predict=1200, num_ctx=8192),
                system="You are the baseline coding agent. Do not call tools.",
            )
            usage = _usage(client)
            return {"success": bool(answer.strip()), **usage, "tool_calls": 0, "retries": 0, "error": ""}
        except Exception as exc:  # benchmark records failures per task
            return {"success": False, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "tool_calls": 0, "retries": 0, "error": f"{type(exc).__name__}: {exc}"}

    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0}

    def generate(prompt: str) -> str:
        response = client.generate(
            prompt,
            model=model,
            config=OllamaGenerationConfig(temperature=0.0, num_predict=1200, num_ctx=16384),
            system="Return exactly one JSON object for the typed repository tool loop.",
        )
        usage = _usage(client)
        usage_totals["prompt_tokens"] += usage["prompt_tokens"]
        usage_totals["completion_tokens"] += usage["completion_tokens"]
        return response

    try:
        run = ToolCallingAgent(
            generate,
            build_default_tool_registry(repository_root=ROOT),
            max_turns=max_turns,
        ).run(task["prompt"], max_turns_override=task.get("suggested_max_turns"))
        total = usage_totals["prompt_tokens"] + usage_totals["completion_tokens"]
        return {
            "success": bool(run.final_answer.strip()) and not run.exhausted,
            **usage_totals,
            "total_tokens": total,
            "tool_calls": len(run.calls),
            "retries": sum(1 for call in run.calls if not call.result.get("ok", True)),
            "error": "turn_limit" if run.exhausted else "",
        }
    except Exception as exc:
        return {"success": False, "prompt_tokens": usage_totals["prompt_tokens"], "completion_tokens": usage_totals["completion_tokens"], "total_tokens": sum(usage_totals.values()), "tool_calls": 0, "retries": 0, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("baseline", "shielded"), required=True)
    parser.add_argument("--model", default="qwen2.5-coder:3b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--max-turns", type=int, default=8)
    args = parser.parse_args()
    task = json.loads(sys.stdin.read())
    result = _run(task, args.mode, args.model, args.ollama_url, args.max_turns)
    print(json.dumps(result))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
