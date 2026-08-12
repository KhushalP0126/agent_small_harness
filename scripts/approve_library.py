from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.library_registry import DEFAULT_LIBRARY_REGISTRY


DEFAULT_PROPOSAL_DIR = ROOT / "data" / "library_proposals"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _next_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return "1.0"
    return f"{parts[0]}.{int(parts[1]) + 1}"


def merge_proposal(registry_path: Path, proposal_path: Path, dry_run: bool = False) -> dict[str, Any]:
    registry = _load_json(registry_path) or {"schema_version": "2.0", "libraries": {}}
    proposal_payload = _load_json(proposal_path)
    if not proposal_payload.get("available"):
        raise ValueError(f"Proposal is not available for approval: {proposal_path}")
    proposal = proposal_payload.get("proposal", {})
    library = proposal.get("library") or proposal_payload.get("library")
    if not library:
        raise ValueError(f"Proposal missing library name: {proposal_path}")
    language = (
        proposal.get("language")
        or proposal_payload.get("language")
        or "python"
    ).strip().lower()

    libraries = registry.setdefault("libraries", {})

    def _is_nested_language_schema(payload: dict[str, Any]) -> bool:
        if not payload:
            return True
        for value in payload.values():
            if not isinstance(value, dict):
                return False
            # Flat library schema has allow-list fields at this level.
            if "allowed_calls" in value or "allowed_constants" in value:
                return False
            if value and not all(isinstance(item, dict) for item in value.values()):
                return False
        return True

    if _is_nested_language_schema(libraries):
        bucket = libraries.setdefault(language, {})
    else:
        # Migrate flat -> nested on write.
        flat = {key: value for key, value in libraries.items()}
        libraries.clear()
        libraries["python"] = flat
        bucket = libraries.setdefault(language, {})
    registry["schema_version"] = registry.get("schema_version") or "2.0"

    existing = bucket.get(library, {})
    existing_calls = set(existing.get("allowed_calls", []))
    proposed_calls = set(proposal.get("allowed_calls", []))
    merged_calls = sorted(existing_calls | proposed_calls)
    added_calls = sorted(proposed_calls - existing_calls)
    bucket[library] = {
        "allowed_calls": merged_calls,
        "context": existing.get("context") or proposal.get("context", ""),
        "unknown_api_repair": existing.get("unknown_api_repair") or proposal.get("unknown_api_repair", ""),
    }
    old_version = str(registry.get("schema_version", "1.0"))
    registry["schema_version"] = _next_version(old_version)
    result = {
        "library": library,
        "old_version": old_version,
        "new_version": registry["schema_version"],
        "existing_call_count": len(existing_calls),
        "proposed_call_count": len(proposed_calls),
        "added_calls": added_calls,
        "registry": registry,
    }
    if not dry_run:
        registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve a discovered library proposal into the trusted registry.")
    parser.add_argument("library", help="Library proposal name, e.g. pandas.")
    parser.add_argument("--proposal-dir", default=str(DEFAULT_PROPOSAL_DIR))
    parser.add_argument("--registry", default=str(DEFAULT_LIBRARY_REGISTRY))
    parser.add_argument("--dry-run", action="store_true", help="Show merge result without writing registry.")
    args = parser.parse_args()

    proposal_path = Path(args.proposal_dir) / f"{args.library}.json"
    result = merge_proposal(Path(args.registry), proposal_path, dry_run=args.dry_run)
    print(f"Library: {result['library']}")
    print(f"Registry version: {result['old_version']} -> {result['new_version']}")
    print(f"Existing calls: {result['existing_call_count']}")
    print(f"Proposed calls: {result['proposed_call_count']}")
    print(f"Added calls: {len(result['added_calls'])}")
    for symbol in result["added_calls"][:50]:
        print(f"- {symbol}")
    if len(result["added_calls"]) > 50:
        print("...")
    if args.dry_run:
        print("Dry run: registry not modified.")


if __name__ == "__main__":
    main()
