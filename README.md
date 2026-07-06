# Agent Coder Structure

Day 1 scaffold for a controlled AI-engineering harness with:

- `benchmarker.py` for empirical complexity checks
- `engines/decomposition_engine.py` for shared structural IR extraction
- `engines/evaluator.py` for scoring engine behavior against code snippets in `data/`
- `agents/` for preprocessing, coding, dependency/scope review, history, and postprocessing
- `agents/parse_contract.py`, `agents/engine_registry.py`, `agents/repair_strategy.py`, `agents/behavior_spec.py` for the typed parser gate, language-based engine routing, repair-mode/template selection, and behavior-spec resolution
- `engines/` for math, hazards, branching, algorithmic-cost, optional lint, and optional C/C++ tree-sitter analysis
- `backends/` for local Ollama worker calls and API-backed architect escalation
- `config.yaml` for declarative policy, model, retry, behavior-timeout, and optional formal-validation defaults
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

Install optional formal/semantic verification tools:

```bash
make install-formal
```

Formal tooling is capability-gated. The Plan layer can emit Deal contract candidates, CrossHair can be enabled as an optional semantic counterexample validator, and Nagini is reserved for architect-tier formalization of small critical helpers.

Run the harder coding-capability harness without any model calls:

```bash
make test-coding-capability-fixture
```

This runs the static engines and behavior validator against seven general Python tasks:

- matrix scoring
- dedupe while preserving order
- clamp values
- merge inclusive intervals
- parse key/value lines
- group top scores
- summarize transactions

## Python-First Harness Workflow

The active product direction is Python-first. C/C++ structural support exists as an optional future path, but the reliable loop is:

```text
prompt -> Plan Mode compact packet -> local worker -> Python parse gate
       -> Python engines -> behavior validator -> optional CrossHair
       -> repair, architect escalation, or human review
```

Plan Mode now feeds the small worker directly. It extracts function names, behavior examples, Deal-style contract examples, state-machine rules for parser/config tasks, and adapter rules for opaque library dependencies.

Useful Python-first commands:

```bash
make test-plan-mode-ladder
make test-worker-limit MODEL=qwen2.5-coder:3b SAVE_ARTIFACTS=1
make test-worker-limit-auto SAVE_ARTIFACTS=1
make test-python-ladder-parsing MODEL=qwen2.5-coder:3b
make test-python-ladder-data MODEL=qwen2.5-coder:3b
make test-python-ladder-algorithmic MODEL=qwen2.5-coder:3b
make test-raw-vs-harness MODEL=qwen2.5-coder:3b
```

`MODEL=auto` uses `config.yaml` difficulty routing:

```yaml
execution:
  models:
    difficulty_models:
      1-2: qwen2.5-coder:1.5b
      3-5: qwen2.5-coder:3b
      6+: qwen2.5-coder:7b
```

When `SAVE_ARTIFACTS=1` is used, each run writes an artifact directory under `artifacts/runs/`. Render a human-review summary with:

```bash
make review-run RUN=<run-id-or-path>
```

The artifact includes `attempt_timeline.json`, the generated drafts, validation reports, retry prompts, and a session summary.

## Configuration

Harness defaults live in `config.yaml`.

```yaml
engines:
  policy:
    max_loop_depth: 2
    max_cyclomatic_complexity: 7
    allow_explicit_globals: false
    allow_module_state_mutation: false
    allow_external_dependencies: false
    allow_unknown_registered_apis: false
    allow_unsafe_calls: false
    allow_algorithmic_hotspots: false
    allow_lint_errors: false
  behavior:
    timeout_seconds: 1.0
  formal:
    crosshair_enabled: false
    crosshair_timeout_seconds: 3.0

execution:
  models:
    worker_model: qwen2.5-coder:1.5b
    architect_model: deepseek-v4-pro
    difficulty_models:
      1-2: qwen2.5-coder:1.5b
      3-5: qwen2.5-coder:3b
      6+: qwen2.5-coder:7b
  gates:
    max_retries: 1
```

The loader is `agents/config_loader.py`. It validates the schema before the harness starts and rejects unknown keys or invalid thresholds. The coding-capability runner accepts:

```bash
python3 scripts/run_coding_capability.py --config config.yaml --supplier fixture
```

Make targets use `CONFIG_PATH=config.yaml` by default:

```bash
make test-coding-capability-fixture CONFIG_PATH=config.yaml
```

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

Run the optional CrossHair smoke experiment:

```bash
make test-formal-experiment
```

If CrossHair is not installed, this reports a clean skip. Install optional formal tooling with `make install-formal`.

## Big-LLM Architect Escalation

The big LLM is attached as a repair worker behind the engines, not as an engine and not as a replacement for validation.

```text
prompt
  -> small local worker writes code
  -> parse contract
  -> engine registry
  -> static policy + behavior validation
  -> small worker repair
  -> if still failing after threshold, API architect repairs
  -> parse contract + engines + validators again
  -> completed or manual_review_required
```

The default architect backend is DeepSeek through an OpenAI-compatible chat completions request. Configure it in a local `.env` file:

```env
DEEPSEEK_API_KEY=your_key_here
ARCHITECT_MODEL=deepseek-v4-pro
```

`.env` is ignored by git. Use `.env.example` as the safe template.

Run the tougher small-worker plus architect path:

```bash
make test-coding-capability-architect
```

Defaults:

- `MODEL=qwen2.5-coder:1.5b`
- `ARCHITECT_MODEL=deepseek-v4-pro`
- `ARCHITECT_AFTER=1`
- `ARCHITECT_MAX_RETRIES=2`
- `CONFIG_PATH=config.yaml`

Override example:

```bash
make test-coding-capability-architect ARCHITECT_MODEL=deepseek-v4-flash ARCHITECT_AFTER=1
```

Current flow:

```text
source -> parse contract -> engine registry -> engines -> policy/behavior/formal validation
       -> repair strategy -> retry prompt -> small worker or architect worker -> historian
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
You are reviewing a local repo that implements a constrained code-generation and repair harness.

Repo purpose:
- Help a smaller local coding model generate and repair code under deterministic quality gates.
- Use engines and behavior specs as the fitness/validation layer.
- Escalate to a larger API-backed architect model only after the controller has evidence that the small worker failed.
- Keep the system generalized for code creation and repair. Snake and terminal games are stress fixtures only.

Current repo structure:
- `agents/`
  - `generation_controller.py`: owns the create/repair loop, stagnation guard, diagnostic deltas, architect escalation, and human-review payloads
  - `plan_mode.py`: extracts compact worker packets, Deal contract examples, state-machine rules, and adapter contracts
  - `engine_registry.py`: routes parsed drafts to registered engines
  - `parse_contract.py`: refuses unsupported or unparsable syntax before engines run
  - `repair_strategy.py`: turns violations into targeted repair instructions
  - `historian.py`: records run samples and aggregates route stats
  - `prompt_normalizer.py`, `task_classifier.py`, and `routing_policy.py`: keep prompts/routing deterministic
- `engines/`
  - `math_engine.py`: loop-depth analysis
  - `hazards_engine.py`: global/module-state mutation, dependency, and registered-library API hazards
  - `branching_engine.py`: cyclomatic complexity and branch density
  - `cost_engine.py`: repeated linear membership and similar algorithmic hotspots
  - `lint_engine.py`: optional Pylint-backed fatal/error checks
  - `treesitter_engine.py`: optional C/C++ structural checks when tree-sitter is installed
  - `evaluator.py`: runs engines against code snippets in `data/snippets/` using expectations in `data/engine_cases.json`
- `validation/`
  - `policy.py`: maps findings to violations
  - `behavior.py`: runs restricted Python behavior specs in a timeout-bound child process
  - `formal.py`: optional CrossHair-backed semantic counterexample validation
- `backends/`
  - `ollama_client.py`: local small-worker model client
  - `architect_client.py`: DeepSeek/OpenAI-compatible architect escalation client using `.env` or shell env secrets; also exposes a Nagini formalization prompt path for architect-tier verification work
- `tests/coding_capability/tasks.json`
  - contains harder general Python tasks with behavioral specs
- `tests/python_ladders/`
  - contains focused parsing, data-transform, and algorithmic Python ladders

Important design intent:
- Engines are the judge, not the LLM.
- DeepSeek/the architect is a repair worker behind the engines, not an engine.
- A draft is accepted only after parse, engine, policy, behavior, and enabled formal gates pass.
- Deal belongs in Plan Mode as executable contract scaffolding; CrossHair belongs in validation; Nagini belongs in architect formalization.
- `.env` is local secret storage for `DEEPSEEK_API_KEY`; real keys must never be committed or logged.

Current strengths:
- Engine evaluator reports `overall_recall: 1.0`.
- Unit suite should be rerun after local changes with `make test`.
- Plan Mode ladder validates compact planning/spec extraction.
- Fixture coding-capability suite passes 7/7 without model calls.
- Live small-worker plus DeepSeek architect test reached 6/7 on harder general tasks.
- The controller records repair workers (`small_worker`, `architect_llm`, or `small_worker->architect_llm`) and refuses bad final output.

Current limitations:
- One hard task, `parse_key_value_lines`, still failed after architect escalation.
- C/C++ support is structural only; compile/run behavior validation is deferred.
- Pylint is optional and skipped when unavailable.
- The architect API can fail or return empty responses, so the controller converts backend errors into `manual_review_required`.

What I want from you:
1. Critique whether the big-LLM architect escalation is attached at the correct layer.
2. Identify why `parse_key_value_lines` may still fail after architect escalation.
3. Recommend how to improve repair prompts without adding task-specific solution templates.
4. Suggest the next highest-leverage hard coding tasks to add.
5. Identify any duplicated logic, brittle validation assumptions, or security risks.

Please stay concrete and repo-aware. Do not give generic multi-agent architecture advice unless it maps directly to the files and behavior described above.
```
