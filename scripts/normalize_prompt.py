from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.prompt_normalizer import PromptNormalizerAgent


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize a conversational prompt for the worker model.")
    parser.add_argument("prompt", nargs="*", help="Prompt text. Reads stdin when omitted.")
    parser.add_argument("--show-removed", action="store_true", help="Print removed filler fragments after the prompt.")
    args = parser.parse_args()

    raw_prompt = " ".join(args.prompt).strip() if args.prompt else sys.stdin.read()
    result = PromptNormalizerAgent().normalize(raw_prompt)
    print(result.normalized_prompt)
    if args.show_removed:
        print("\nRemoved fragments:")
        for fragment in result.removed_fragments:
            print(f"- {fragment}")


if __name__ == "__main__":
    main()
