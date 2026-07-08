# Repository Structure

This repo is a generalized code-generation and repair harness. The core flow is:

```text
raw user prompt -> prompt normalizer -> task classifier -> plan/deal context -> routing policy -> model draft -> parse contract -> engine registry -> engines -> policy/behavior/formal validation -> repair loop -> completion, architect escalation, or human review
```

Snake files are smoke-test fixtures only. The harness is intended to stay task-agnostic.

## Root Files

- `README.md` - Project overview and basic command surface.
- `Makefile` - Common commands for install, tests, adversarial PEV traps, Plan Mode ladders, Python worker ladders, raw-vs-harness comparison, review summaries, engine evaluation, Ollama smoke checks, and live repair.
- `config.yaml` - Declarative harness configuration for engine policy, behavior timeout, optional CrossHair formal validation, worker/architect models, difficulty-based model routing, routing thresholds, and retry gates.
- `.env` - Local ignored secrets file. Put `DEEPSEEK_API_KEY` here for API-backed architect escalation.
- `.env.example` - Safe committed template showing the supported environment keys.
- `benchmarker.py` - Day 1 orchestration entrypoint, benchmark helper, and `build_ollama_controller()` factory.
- `requirements.txt` - Optional dependency manifest, mainly for tree-sitter C/C++ support.
- `requirements-formal.txt` - Optional Deal/CrossHair/Pylint dependency manifest. Nagini remains an architect-tier external toolchain target.
- `history.json` - Historian persistence file for generation records, repair outcomes, and learned successful templates.
- `conventions.md` - Static conventions injected by the preprocessor/coder context.
- `context.md` - Project context for prompt construction and architectural continuity.
- `design.md` - Design and architectural constraints injected into repair prompts when relevant.
- `structure.md` - This file.

## `agents/`

The agent layer coordinates the harness. These are deterministic orchestration components, not free-running autonomous agents.

- `agents/base.py` - Shared `AgentResult` and `BaseAgent` types.
- `agents/config_loader.py` - Strict dataclass-backed config loader for `config.yaml`. Parses the supported YAML subset and rejects unknown keys or invalid thresholds.
- `agents/preprocessor.py` - Loads conventions/context before generation.
- `agents/prompt_normalizer.py` - Deterministically removes conversational filler from raw user prompts before worker-model generation.
- `agents/task_classifier.py` - Classifies normalized prompts by task type, likely language, library hints, and behavior-spec need.
- `agents/plan_mode.py` - Deterministic Plan Mode facade. Extracts target functions, behavior examples, compact worker packets, state-machine constraints, dependency adapter contracts, clarification questions, and Deal contract candidates for architect/spec use.
- `agents/routing_policy.py` - Chooses small worker, template-assisted worker, or architect escalation based on classification and human-review payloads.
- `agents/coder.py` - Builds the initial repair/generation prompt, including context files, behavior specs, and templates.
- `agents/parse_contract.py` - Language detection and parser gate. Returns parse success or a typed parse/unsupported-language failure.
- `agents/engine_registry.py` - Routes parsed drafts to the correct engine set by language.
- `agents/generation_controller.py` - Main repair loop. Runs drafts through parse, engines, policy validation, behavior validation, optional CrossHair formal validation, retries, stagnation guard, diagnostic deltas, and human-review escalation payloads.
- `agents/repair_strategy.py` - Converts validation failures into repair instructions and chooses model-only, template-directed, or manual-review paths.
- `agents/repair_templates.py` - Optional repair templates for explicit template-directed experiments. Normal coding-capability tests should measure model capability rather than route through task-specific solution templates.
- `agents/behavior_spec.py` - Loads and resolves behavioral test specs from `data/behavior_cases.json`.
- `agents/historian.py` - Records repair outcomes, emits append-only run samples, aggregates route stats, and promotes successful template usage into reusable lessons.
- `agents/job_store.py` - Append-only JSONL job store for queue-style orchestration and persistent run status.
- `agents/library_discovery.py` - Discovers importable library public symbols without importing the library and emits reviewable registry proposals.
- `agents/dependency.py` - Dependency-context helper agent.
- `agents/scope_tracker.py` - Scope-context helper agent.
- `agents/postprocessor.py` - Final polish/output wrapper.
- `agents/template_loader.py` - Loads language-specific skeleton templates from `templates/`.
- `agents/__init__.py` - Package marker.

## `engines/`

The engine layer performs structural analysis. Python uses the standard-library `ast`; C/C++ use optional tree-sitter support.

- `engines/base.py` - Shared `BaseEngine`, `EngineFinding`, and `EngineDiagnostic` types.
- `engines/decomposition_engine.py` - Python AST-to-structural-IR pass used by the Python engines.
- `engines/math_engine.py` - Loop-depth analysis for growth-risk detection.
- `engines/hazards_engine.py` - Python hazard checks: global mutation, module-state mutation, external imports, and registered-library unknown API calls.
- `engines/branching_engine.py` - Python cyclomatic complexity and branch-density analysis.
- `engines/cost_engine.py` - Python algorithmic-cost checks, currently repeated linear membership inside loops.
- `engines/bounds_engine.py` - Python warning-first bounds-safety checks for high-confidence one-past-end read/write patterns such as `xs[len(xs)]` and `range(len(xs) + 1)`.
- `engines/state_flow_engine.py` - Python state-propagation check for helpers that assign to state-like parameters without returning the updated state.
- `engines/lint_engine.py` - Optional Pylint-backed lint engine. Blocks only fatal/error messages and skips cleanly when Pylint is unavailable.
- `engines/evaluator.py` - Evaluates engine findings against `data/engine_cases.json`.
- `engines/library_registry.py` - Loads `data/library_registry.json` for registered-library API validation.
- `engines/treesitter_support.py` - Optional tree-sitter loader, parser factory, parse cache, and parse-error discovery.
- `engines/treesitter_engine.py` - C/C++ structural engines for loop depth, branching complexity, and unsafe API calls.
- `engines/__init__.py` - Package marker.

## `validation/`

The validation layer converts findings into pass/fail decisions.

- `validation/types.py` - `Violation`, `ValidationResult`, violation kinds, and repair-hint types.
- `validation/policy.py` - Maps engine findings to policy violations based on thresholds and allow/deny settings.
- `validation/behavior.py` - Behavioral validator for Python function specs. Executes restricted generated Python in a timeout-bound child process against test cases.
- `validation/formal.py` - Optional CrossHair semantic validator. Skips cleanly when CrossHair is not installed; when enabled and available, counterexamples become formal repair violations.
- `validation/__init__.py` - Package marker.

## `prompt/`

Prompt builders turn engine and policy context into model-facing instructions.

- `prompt/constraint_types.py` - Typed constraint-block dataclasses for loop, branch, mutation, dependency, and lesson context.
- `prompt/builder.py` - Builds the initial structured generation prompt from a `ConstraintBlock`.
- `prompt/retry_builder.py` - Builds targeted repair prompts from violations and the current draft.
- `prompt/__init__.py` - Package marker.

## `backends/`

Model backend integrations.

- `backends/ollama_client.py` - Local Ollama HTTP client, model supplier, response cleanup, and fenced-code extraction.
- `backends/architect_client.py` - API-backed architect repair client. Defaults to DeepSeek, reads `DEEPSEEK_API_KEY` or `ARCHITECT_API_KEY` from the shell or `.env`, extracts code from architect responses, and includes a Nagini-oriented formalization prompt method for architect-tier proof candidates.
- `backends/__init__.py` - Package marker.

## `data/`

Ground-truth cases, behavior specs, library schemas, and sample snippets.

- `data/engine_cases.json` - Expected engine findings for Python fixture snippets.
- `data/behavior_cases.json` - Behavioral input/output specs used by the behavior validator.
- `data/library_registry.json` - Registered library API schemas and adapter-contract guidance for libraries such as `pygame`, `pandas`, and `sqlalchemy`.
- `data/library_proposals/` - Optional untrusted discovery proposals. Engines do not read these until explicitly approved into `library_registry.json`.
- `data/runs.jsonl` - Optional append-only raw run log produced by historian workflows.
- `data/stats.json` - Optional aggregated routing stats generated from `data/runs.jsonl`.

### `data/snippets/`

Python engine fixtures:

- `data/snippets/linear_safe.py` - Simple linear safe case.
- `data/snippets/nested_loop.py` - Nested loop case.
- `data/snippets/triple_nested.py` - Loop-depth violation case.
- `data/snippets/branchy_but_safe.py` - Medium-branching safe case.
- `data/snippets/global_in_helper.py` - Explicit global mutation fixture.
- `data/snippets/global_and_branch_heavy.py` - Global mutation plus high branching fixture.
- `data/snippets/mixed_hard_case.py` - Combined static and behavior repair fixture.

C/C++ tree-sitter fixtures:

- `data/snippets/c/simple.c` - Simple C parse/complexity fixture.
- `data/snippets/c/nested_branchy.c` - C loop/branch complexity fixture.
- `data/snippets/c/unsafe.c` - C unsafe API fixture.
- `data/snippets/cpp/simple.cpp` - Simple C++ parse/complexity fixture.
- `data/snippets/cpp/nested_branchy.cpp` - C++ loop/branch complexity fixture.

## `templates/`

Language-specific skeletons used as optional generation seeds.

- `templates/snake/python/snake.py` - Python Snake smoke-test skeleton.
- `templates/snake/c/snake.c` - C Snake smoke-test skeleton.
- `templates/snake/cpp/snake.cpp` - C++ Snake smoke-test skeleton.

## `scripts/`

Manual smoke-test and integration scripts.

- `scripts/test_inference.py` - Checks whether the configured Ollama model responds.
- `scripts/normalize_prompt.py` - CLI helper for normalizing a prompt before sending it to the worker model.
- `scripts/aggregate_history.py` - Aggregates `data/runs.jsonl` into `data/stats.json` for future routing decisions.
- `scripts/discover_library.py` - Writes a reviewable proposal into `data/library_proposals/` for an installed/importable library.
- `scripts/approve_library.py` - Explicitly merges an approved proposal into the trusted `data/library_registry.json`.
- `scripts/run_adversarial_prompts.py` - Runs deterministic trap prompts through the controller and appends historian run samples.
- `scripts/run_coding_capability.py` - Runs small-worker coding tasks through model generation, static engines, behavior checks, optional architect escalation, and optional historian logging.
- `scripts/run_worker_limit.py` - Runs the harder-and-harder local worker ladder. Supports explicit `MODEL=...`, `MODEL=auto`, artifact saving, optional decomposition prompts, and optional architect escalation.
- `scripts/run_plan_mode_ladder.py` - Tests deterministic Plan Mode extraction quality without model calls.
- `scripts/run_raw_vs_harness.py` - Compares raw one-shot model behavior validation with the full harness loop on the same tasks.
- `scripts/run_formal_experiment.py` - Runs a tiny optional CrossHair semantic-validation smoke experiment and skips cleanly when CrossHair is missing.
- `scripts/review_run.py` - Renders a concise human-review summary for an artifact run directory or run id.
- `scripts/run_live_repair.py` - Runs the live repair loop against a fixture, usually `mixed_hard_case.py`.
- `scripts/test_snake_generation.py` - Asks the model for Snake implementations and runs parse/engine checks across Python/C/C++.

## `tests/`

Unit and integration tests.

- `tests/test_benchmarker.py` - Core benchmarker, controller, prompt, behavior, Ollama supplier, and repair-loop tests.
- `tests/test_agents_pipeline.py` - Agent pipeline tests for parse contracts, registry routing, repair strategy, historian, library API validation, and controller integration.
- `tests/test_behavior.py` - Behavior validator tests, including static-clean hallucination rejection.
- `tests/test_treesitter_pipeline.py` - Optional C/C++ tree-sitter parse, engine, registry, and gating tests.
- `tests/adversarial/prompts.json` - Trap prompt fixtures for parse failure, global mutation, loop depth, branch complexity, and algorithmic cost.
- `tests/coding_capability/tasks.json` - Small-worker code-generation tasks with behavioral input/output specs. Current tasks cover matrix scoring, order-preserving dedupe, clamping, interval merging, key/value parsing, grouped top scores, and transaction summarization.
- `tests/worker_limit/tasks.json` - Harder local-worker ladder used to find the small model's practical breaking point.
- `tests/worker_limit/decompositions.json` - Optional decomposition skeletons for worker-limit experiments.
- `tests/plan_mode/tasks.json` - Deterministic Plan Mode extraction ladder, including library context and state-machine parser cases.
- `tests/python_ladders/parsing.json` - Focused Python parsing ladder.
- `tests/python_ladders/data_transform.json` - Focused Python grouping/aggregation ladder.
- `tests/python_ladders/algorithmic.json` - Focused Python algorithmic ladder.
- `tests/python_ladders/stateful.json` - Focused Python stateful parser/event ladder.

## Important Current Guarantees

- Every valid Python draft should pass through the Python engine set: math, hazards, branching, algorithmic cost, bounds, state-flow, and optional lint.
- Parseable code is not enough to complete; the controller also requires a registered engine set for that language.
- Unknown registered-library API calls, such as `pygame.rect(...)`, become `unknown_api` violations.
- If a small-worker repair returns unchanged code and architect escalation is configured, the controller can immediately try the architect worker. If the architect is unavailable, fails, or returns unchanged code, the run escalates to `manual_review_required`.
- The architect worker is not an engine. It receives engine/behavior feedback and its output is rescanned by the same gates.
- Manual review results include a structured `human_review` payload with blocking findings, violations, behavior issues, diagnostic deltas, and suggested next action.
- Artifact runs include `attempt_timeline.json` so review tools can show attempt-by-attempt worker, static, behavior, formal, diff, and retry-prompt status without scanning every file manually.
- `scripts/review_run.py` renders root-cause candidates from the latest static, behavior, and formal validation artifacts.
- Deal, CrossHair, and Nagini are deliberately separated: Deal is plan/spec scaffolding, CrossHair is optional semantic validation, and Nagini is an architect-tier formalization target for critical helpers rather than a default small-worker gate.
