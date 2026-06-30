from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backends.ollama_client import DEFAULT_OLLAMA_MODEL, OllamaModelSupplier


def run_inference_check(model: str = DEFAULT_OLLAMA_MODEL) -> str:
    supplier = OllamaModelSupplier(model=model)
    return supplier.generate_draft("Reply with only the word READY.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify that the local Ollama model returns a response.")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    args = parser.parse_args()

    print(f"Testing Ollama inference with {args.model}...")
    response = run_inference_check(model=args.model)
    print("Model response:")
    print(response.strip())


if __name__ == "__main__":
    main()
