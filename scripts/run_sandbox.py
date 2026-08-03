from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness_kernel.container_sandbox import run_source_isolated


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute generated source in a bounded sandbox.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--language", required=True)
    parser.add_argument("--mode", choices=("container", "local"), default="container")
    parser.add_argument("--runtime", choices=("docker", "podman"), default="docker")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--allow-local-fallback", action="store_true")
    args = parser.parse_args()
    result = run_source_isolated(
        args.source.read_text(encoding="utf-8"),
        args.language,
        timeout_seconds=args.timeout,
        mode=args.mode,
        runtime=args.runtime,
        network_enabled=args.network,
        allow_local_fallback=args.allow_local_fallback,
    )
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.returncode == 0 and not result.timed_out else 1


if __name__ == "__main__":
    raise SystemExit(main())
