# Agent Coder Structure

Day 1 scaffold for a controlled AI-engineering harness with:

- `benchmarker.py` for empirical complexity checks
- `engines/decomposition_engine.py` for shared structural IR extraction
- `engines/evaluator.py` for scoring engine behavior against code snippets in `data/`
- `agents/` for preprocessing, coding, dependency/scope review, history, and postprocessing
- `agents/parse_contract.py`, `agents/engine_registry.py`, `agents/repair_strategy.py`, `agents/behavior_spec.py` for the typed parser gate, language-based engine routing, repair-mode/template selection, and behavior-spec resolution
- `engines/` for math, hazards, and branching analysis
- `prompt/` for turning engine output into structured generation prompts
- `data/snippets/` for sample source files that engines scan
- `history.json` as the historian knowledge base
- `conventions.md` as the injected operating contract

## Quick start

Run the benchmark demo:

```bash
python3 benchmarker.py
```

Evaluate the engine suite directly:

```bash
python3 -c "from dataclasses import asdict; from engines.evaluator import evaluate_engines; import json; print(json.dumps(asdict(evaluate_engines()), indent=2))"
```

The evaluator reads sample files from `data/snippets/` using expectations defined in `data/engine_cases.json`.

Run tests:

```bash
python3 -m unittest discover -s tests
```

Run the behavior gate:

```bash
make test-behavior
```

This checks explicit input-output behavior for fixture functions and catches static-clean hallucinations, such as replacing `analyze(matrix)` with a constant return.

Use the local Ollama backend with the small quantized coder model:

```bash
ollama pull qwen2.5-coder:1.5b
python3 -c "from benchmarker import build_ollama_controller; controller = build_ollama_controller(debug=True); print(controller.name)"
```

The default backend model is `qwen2.5-coder:1.5b`. In Ollama, published tags are already quantized builds, so keeping the harness on the smaller tag is the practical way to stay memory-efficient. Override with `OLLAMA_MODEL=...` if needed.

Run a real one-call inference check after the model is installed:

```bash
make inference-smoke
```

Run the first live repair loop against the hard fixture:

```bash
make live-repair
```

Current flow:

```text
source -> parse contract (language gate) -> engine registry (math/hazards/branching) -> validation
       -> repair strategy (model-only | template-directed | manual review) -> retry prompt -> model -> historian
```

The parse contract refuses to run engines against syntax it cannot parse, so new languages are unlocked by registering an engine set rather than editing the controller. The historian records which templates/prompts produced a compliant draft and promotes them into reusable lessons; run `make live-repair` with `--record-history` to persist outcomes into `history.json`.
## Multi-language support (C / C++)
Python is analyzed with the stdlib `ast` engines. C and C++ are analyzed with `tree-sitter` (an optional dependency) through engines that emit the same `engine-1-math` / `engine-2-hazards` / `engine-3-branching` findings, so structural policy and the controller are language-agnostic.
Install the optional grammars:
```bash
make install   # or: python3 -m pip install -r requirements.txt
```
With tree-sitter installed, `EngineRegistry.default()` routes `c`/`cpp` to the tree-sitter engines (cyclomatic complexity, loop-nesting depth, and an unsafe-API hazard check such as `gets`/`system`/`strcpy`). If tree-sitter is **not** installed, C/C++ are gracefully gated by the parse contract and routed to manual review, and the rest of the suite still passes. Skeletal generation seeds live under `templates/<task>/<language>/` (e.g. `templates/snake/{python,c,cpp}/`). Cross-language behavior validation (compile + run) is not yet implemented; structural analysis only.
Run the C/C++ pipeline tests:
```bash
make test-treesitter
```

## Claude Prompt

Use this prompt when you want Claude to review or advise on this harness:

```text
You are reviewing a local Python repo that implements a scaffold for a constrained code-generation harness.

Repo purpose:
- Help a smaller coding model generate more reliable code by giving it structured constraints instead of raw unfiltered context.
- Treat static analysis findings as prompt-building inputs, not just human-readable reports.
- Keep the system grounded in explicit structure such as loop depth, branching complexity, and state mutation hazards.

Current repo structure:
- `agents/`
  - `preprocessor.py`: injects goal and conventions
  - `coder.py`: currently a stub that returns an implementation plan, not a real model prompt yet
  - `dependency.py`: reports dependency-file context
  - `scope_tracker.py`: reports shared/global scope constraints
  - `historian.py`: loads and appends history in `history.json`
  - `postprocessor.py`: returns final artifact/doc status
- `engines/`
  - `math_engine.py`: reports loop nesting depth and deepest loop path
  - `hazards_engine.py`: reports explicit `global` usage and module-level container mutation hazards
  - `branching_engine.py`: reports cyclomatic complexity, conditional branch count, and risk level
  - `evaluator.py`: runs engines against code snippets in `data/snippets/` using expectations in `data/engine_cases.json`
- `benchmarker.py`
  - runs the Day 1 pipeline
  - benchmarks a linear function
  - runs the engines
  - stores generation records in `history.json`
- `data/snippets/`
  - contains sample Python files used to test the engines
- `tests/test_benchmarker.py`
  - validates benchmark behavior and engine expectations

Important design intent:
- This is not primarily a hallucination detector.
- It is a scaffolding harness meant to externalize reasoning for a smaller model.
- The engines are supposed to compute structured constraints that can later be fed into a prompt builder.
- Right now the main missing piece is a prompt-construction layer that converts engine findings into a compact generation prompt.

Current strengths:
- Real snippet-based evaluation instead of inline toy strings
- Engine metrics are more structured now:
  - loop depth instead of just nested/not nested
  - cyclomatic complexity instead of just branch count
  - module-state mutation hazards beyond explicit globals
- The repo already has a clean place to insert prompt assembly between engine output and code generation

Current limitations:
- `CoderAgent` does not yet build a real prompt for a model
- Findings do not yet include full source anchors, parser provenance, or formal parse-contract types
- The harness is Python-centric and assumes Python AST parsing

What I want from you:
1. Critique the harness as a scaffold for reliable small-model code generation.
2. Suggest how to turn engine output into a structured prompt format.
3. Identify which current fields are useful prompt inputs and which are noise.
4. Recommend the next concrete refactor with the highest leverage.

Please stay concrete and repo-aware. Do not give generic multi-agent architecture advice unless it maps directly to the files and behavior described above.
```
