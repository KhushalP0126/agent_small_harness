from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.config_loader import DEFAULT_CONFIG_PATH, load_config
from agents.library_doc_search import LibraryDocumentationSearchAgent
from agents.library_discovery import LibraryDiscoveryAgent
from agents.kernel_doc_search import KernelLibraryDocumentationSearchAgent
from backends.architect_client import ArchitectApiClient, ArchitectConfig
from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaClient, OllamaGenerationConfig


DEFAULT_PROPOSAL_DIR = ROOT / "data" / "library_proposals"


def _documentation_search_agent(provider: str, model: str, config_path: Path) -> LibraryDocumentationSearchAgent | None:
    if provider == "none":
        return None
    if provider == "qwen":
        selected_model = model or DEFAULT_OLLAMA_MODEL
        client = OllamaClient()

        def qwen_generate(prompt: str) -> str:
            return client.generate(
                prompt=prompt,
                model=selected_model,
                config=OllamaGenerationConfig(temperature=0.0, num_predict=1200, num_ctx=8192),
                system="You are a library documentation research agent. Return JSON only.",
            )

        return LibraryDocumentationSearchAgent(
            provider="qwen", model=selected_model, generate_text=qwen_generate
        )
    if provider == "deepseek":
        harness_config = load_config(config_path)
        architect_config = ArchitectConfig(repair_profile=harness_config.execution.architect.repair)
        client = ArchitectApiClient(architect_config)
        profile = architect_config.repair_profile_from_env
        selected_model = model or profile.model
        if selected_model != profile.model:
            profile = type(profile)(
                model=selected_model,
                timeout_seconds=profile.timeout_seconds,
                temperature=profile.temperature,
                max_tokens=profile.max_tokens,
                thinking_type=profile.thinking_type,
                reasoning_effort=profile.reasoning_effort,
            )

        def deepseek_generate(prompt: str) -> str:
            return client.generate(
                prompt=prompt,
                system="You are a library documentation research agent. Return JSON only.",
                profile=profile,
            )

        return LibraryDocumentationSearchAgent(
            provider="deepseek", model=selected_model, generate_text=deepseek_generate
        )
    if provider == "kernel":
        if model:
            raise ValueError("DOC_MODEL is not used with the kernel browser provider")
        return KernelLibraryDocumentationSearchAgent()
    raise ValueError(f"Unsupported documentation agent: {provider}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover a library surface and write a reviewable proposal.")
    parser.add_argument("library", help="Importable Python package name, e.g. json or pandas.")
    parser.add_argument("--proposal-dir", default=str(DEFAULT_PROPOSAL_DIR))
    parser.add_argument(
        "--doc-agent",
        choices=("none", "qwen", "deepseek", "kernel"),
        default="none",
        help="Use DeepSeek, Qwen, or a Kernel browser to find documentation candidates for the proposal.",
    )
    parser.add_argument("--doc-model", default="", help="Override the documentation-search model name.")
    parser.add_argument(
        "--doc-output",
        default="",
        help="Write a model-generated Markdown syntax guide to this path.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    documentation_search = _documentation_search_agent(args.doc_agent, args.doc_model, args.config)
    agent = LibraryDiscoveryAgent(documentation_search=documentation_search)
    path = agent.write_proposal(args.library, Path(args.proposal_dir))
    payload = json.loads(path.read_text(encoding="utf-8"))
    proposal = payload.get("proposal", {})
    if args.doc_output:
        if documentation_search is None:
            raise SystemExit("--doc-output requires --doc-agent qwen, deepseek, or kernel")
        doc_path = Path(args.doc_output)
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(
            documentation_search.syntax_notes(
                args.library,
                public_symbols=payload.get("public_symbols", []),
                documentation=proposal.get("documentation", []),
            ),
            encoding="utf-8",
        )
        proposal["documentation_file"] = str(doc_path)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote proposal: {path}")
    print(f"Available: {payload.get('available')}")
    print(f"Origin: {payload.get('origin', '')}")
    environment = payload.get("environment", {})
    print(f"Architect API configured: {environment.get('architect_api_key_configured', False)}")
    if environment.get("architect_api_key_env"):
        print(f"Architect API key env: {environment['architect_api_key_env']}")
    print(f"Architect env file: {environment.get('env_file', '.env')}")
    doc_search = proposal.get("documentation_search", {})
    if doc_search:
        print(
            "Documentation search: "
            f"{doc_search.get('provider')}:{doc_search.get('model')} "
            f"docs={len(proposal.get('documentation', []))}"
        )
        if doc_search.get("error"):
            print(f"Documentation search error: {doc_search['error']}")
    print(f"Candidate allowed calls: {len(proposal.get('allowed_calls', []))}")
    for symbol in proposal.get("allowed_calls", [])[:25]:
        print(f"- {symbol}")
    if len(proposal.get("allowed_calls", [])) > 25:
        print("...")


if __name__ == "__main__":
    main()
