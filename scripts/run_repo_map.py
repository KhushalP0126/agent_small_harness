from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.artifact_manager import ArtifactManager
from agents.repo_map_agent import RepoMapAgent


DEFAULT_ARTIFACT_ROOT = Path("artifacts/runs")


def run_repo_map(
    repo_root: Path,
    output_format: str,
    artifact_root: Path,
    save_artifacts: bool,
) -> int:
    agent = RepoMapAgent()
    graph = agent.map_repo(repo_root)

    if output_format == "json":
        rendered = json.dumps(asdict(graph), indent=2)
    elif output_format == "mermaid":
        rendered = agent.to_mermaid(graph)
    else:
        rendered = "\n".join(agent.to_plan_context(graph))
    print(rendered)

    if save_artifacts:
        manager = ArtifactManager(artifact_root)
        paths = manager.create_run(prefix=f"repo_map_{Path(repo_root).name or 'root'}")
        (paths.run_dir / "repo_map.json").write_text(json.dumps(asdict(graph), indent=2), encoding="utf-8")
        (paths.run_dir / "repo_map.mmd").write_text(agent.to_mermaid(graph), encoding="utf-8")
        print(f"\n[repo-map] artifacts written to {paths.run_dir}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Map a Python repository with the AST repo mapper.")
    parser.add_argument("root", type=Path, nargs="?", default=ROOT, help="Repository root to map (defaults to this repo).")
    parser.add_argument("--format", choices=["json", "mermaid", "context"], default="context")
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--save-artifacts", action="store_true")
    args = parser.parse_args()
    return run_repo_map(
        repo_root=args.root,
        output_format=args.format,
        artifact_root=args.artifact_root,
        save_artifacts=args.save_artifacts,
    )


if __name__ == "__main__":
    raise SystemExit(main())
