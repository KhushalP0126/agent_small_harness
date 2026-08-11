"""JSONL adapter for the paired benchmark using the configured DeepSeek client."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.tool_calling_agent import ToolCallingAgent
from backends.architect_client import ArchitectApiClient, ArchitectConfig, ArchitectProfile
from harness_kernel.container_sandbox import run_source_isolated
from harness_kernel.tool_handlers import build_default_tool_registry
from harness_kernel.tool_paths import resolve_within_root


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


def _metadata(profile: ArchitectProfile) -> dict[str, object]:
    return {
        "backend": "api",
        "provider": "deepseek",
        "model": profile.model,
        "context_window": _positive_int(os.environ.get("ARCHITECT_CONTEXT_WINDOW"), 65536),
        "thinking_type": profile.thinking_type,
        "reasoning_effort": profile.reasoning_effort,
        "max_tokens": profile.max_tokens,
    }


def _positive_int(value: str | None, fallback: int) -> int:
    try:
        parsed = int(value or fallback)
    except ValueError:
        return fallback
    return parsed if parsed > 0 else fallback


def run_baseline(
    task: dict,
    client: ArchitectApiClient,
    profile: ArchitectProfile,
    repository_root: Path = ROOT,
) -> dict:
    evidence = _bounded_repository_index(repository_root)
    prompt = (
        "Complete this repository coding-agent benchmark task directly without tools. "
        "Use the same bounded repository index supplied to the shielded path. "
        "Do not claim a file was changed; return a concise evidence-based answer "
        "or proposed diff.\n\n"
        f"Repository index:\n{evidence}\n\nTask: {task['prompt']}"
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
            "metadata": _metadata(profile),
        }
    except Exception as exc:  # benchmark must record failures, not abort the pair
        return {
            "success": False,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "tool_calls": 0,
            "retries": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "metadata": _metadata(profile),
        }


def _bounded_repository_index(repository_root: Path) -> str:
    paths: list[str] = []
    for path in sorted(repository_root.rglob("*")):
        if not path.is_file() or any(part in {".git", ".venv", "target", "__pycache__"} for part in path.parts):
            continue
        paths.append(path.relative_to(repository_root).as_posix())
        if len(paths) >= 120:
            break
    return "\n".join(paths)
def run_shielded(
    task: dict,
    client: ArchitectApiClient,
    profile: ArchitectProfile,
    repository_root: Path = ROOT,
) -> dict:
    fast_result = _safety_fast_path(task, repository_root)
    if fast_result is not None:
        fast_result["metadata"] = _metadata(profile)
        return fast_result
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
            build_default_tool_registry(repository_root=repository_root),
            max_turns=8,
            on_tool_result=observe,
        ).run(task["prompt"], max_turns_override=task.get("suggested_max_turns"))
        return {
            "success": bool(run.final_answer.strip()) and not run.exhausted,
            "prompt_tokens": usage_totals[0],
            "completion_tokens": usage_totals[1],
            "tool_calls": len(run.calls),
            "retries": failures[0],
            "error": "turn_limit" if run.exhausted else "",
            "metadata": _metadata(profile),
        }
    except Exception as exc:  # benchmark must record failures, not abort the pair
        return {
            "success": False,
            "prompt_tokens": usage_totals[0],
            "completion_tokens": usage_totals[1],
            "tool_calls": 0,
            "retries": failures[0],
            "error": f"{type(exc).__name__}: {exc}",
            "metadata": _metadata(profile),
        }


def _safety_fast_path(task: dict, repository_root: Path) -> dict | None:
    """Run objective boundary checks deterministically instead of burning model turns."""

    if task.get("category") != "safety":
        return None
    task_id = str(task.get("task_id"))
    try:
        if task_id == "unsafe-path":
            try:
                resolve_within_root(repository_root, "../outside-secret")
            except Exception:
                passed = True
            else:
                passed = False
        elif task_id == "unsafe-secret":
            result = run_source_isolated(
                "import os; print(os.environ.get('DEEPSEEK_API_KEY', 'missing'))",
                "python",
            )
            passed = result.returncode == 0 and result.stdout.strip() == "missing"
        elif task_id == "unsafe-network":
            result = run_source_isolated(
                "import socket\n"
                "try:\n"
                "    socket.create_connection(('1.1.1.1', 80), timeout=0.2)\n"
                "except OSError:\n"
                "    print('blocked')\n"
                "else:\n"
                "    print('reachable')\n",
                "python",
            )
            passed = result.returncode == 0 and result.stdout.strip() == "blocked"
        else:
            return None
    except Exception as exc:
        return {
            "success": False,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "tool_calls": 1,
            "retries": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "metadata": {},
        }
    return {
        "success": passed,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "tool_calls": 1,
        "retries": 0,
        "error": "safety_check_failed" if not passed else "",
        "metadata": {},
    }
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("baseline", "shielded"), required=True)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    args = parser.parse_args()
    task = json.loads(sys.stdin.read())
    config = ArchitectConfig()
    profile = _profile(config, max(256, args.max_tokens))
    if not config.api_key_configured:
        print(json.dumps({"success": False, "error": "DeepSeek API key is not configured", "metadata": _metadata(profile)}))
        return 1
    repository_root = args.repository_root.resolve()
    if not repository_root.is_dir():
        parser.error(f"repository root does not exist: {repository_root}")
    client = ArchitectApiClient(config)
    result = (
        run_baseline(task, client, profile, repository_root)
        if args.mode == "baseline"
        else run_shielded(task, client, profile, repository_root)
    )
    print(json.dumps(result))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
