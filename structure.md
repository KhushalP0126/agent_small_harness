# Repository Structure

This repository is a generalized Python-first code-generation and repair
harness. The shared flow is:

```text
raw prompt
  -> prompt normalization
  -> task classification
  -> Plan Mode / TaskIR
  -> execution kernel
  -> worker or architect model
  -> parse contract
  -> static engines
  -> policy, behavior, and optional formal validation
  -> repair, completion, or manual review
```

The harness should stay task-agnostic. Domain-specific examples belong in test
fixtures or external experiment specs, not in the controller, Plan Mode, or
engine logic.

## Root Files

- `README.md` - Professional project overview, architecture, setup, and command surface.
- `Makefile` - Common setup, test, ladder, model, history, review, and smoke commands.
- `config.yaml` - Declarative policy, retry, model, behavior, and routing settings.
- `pyproject.toml` - Python package metadata and runtime dependencies.
- `Dockerfile` - Container entrypoint for the synchronous API service.
- `.dockerignore` - Excludes local secrets, caches, artifacts, and build output from image context.
- `.github/workflows/ci.yml` - GitHub Actions workflow for package install, tests, and Docker image build.
- `.env.example` - Safe template for local secrets such as architect API keys.
- `benchmarker.py` - Benchmark entrypoint and helper factory for Ollama-backed controllers.
- `requirements.txt` - Optional tree-sitter dependency manifest.
- `requirements-kernel.txt` - Optional Kernel browser documentation dependency manifest.
- `requirements-formal.txt` - Optional Deal, CrossHair, and Pylint dependency manifest.
- `history.json` - Historian persistence file for run summaries and successful repair lessons.
- `conventions.md` - Stable model-facing coding and harness rules.
- `context.md` - Current project context, decisions, and experiment notes.
- `design.md` - Architecture, safety, escalation, and validation design constraints.
- `structure.md` - This file.

## `agents/`

Deterministic orchestration components. These are not free-running autonomous
agents; they prepare, route, validate, and record work.

- `agents/base.py` - Shared `AgentResult` and `BaseAgent` types.
- `agents/config_loader.py` - Strict dataclass-backed `config.yaml` loader.
- `agents/preprocessor.py` - Loads context and convention files before generation.
- `agents/prompt_normalizer.py` - Removes conversational filler from raw prompts.
- `agents/task_classifier.py` - Infers task type, language, library hints, and route hints.
- `agents/plan_mode.py` - Extracts target functions, behavior examples, state rules, graph context, adapter contracts, Deal candidates, and `TaskIR`.
- `agents/template_registry.py` - Optional injected template-route selector. It has no built-in app-specific route.
- `agents/routing_policy.py` - Chooses worker, template-assisted worker, architect escalation, or manual review.
- `agents/coder.py` - Builds initial model prompts from context and behavior specs.
- `agents/parse_contract.py` - Language detection and parser gate.
- `agents/engine_registry.py` - Routes parseable source to the registered engine set.
- `agents/generation_controller.py` - Main loop for drafting, validation, repair, branch-loop detection, architect fallback, and final status.
- `agents/repair_strategy.py` - Turns validation failures into targeted repair directives.
- `agents/behavior_spec.py` - Loads behavior specs from `data/behavior_cases.json`.
- `agents/historian.py` - Records raw runs and aggregates route statistics.
- `agents/job_store.py` - Append-only JSONL job store.
- `agents/library_discovery.py` - Generates reviewable library registry proposals.
- `agents/library_doc_search.py` - Model-backed documentation search and Markdown syntax-guide generation for library proposals.
- `agents/kernel_doc_search.py` - Kernel browser-backed documentation search that verifies fetched page text.
- `agents/dependency.py` - Dependency-context helper.
- `agents/scope_tracker.py` - Scope-context helper.
- `agents/postprocessor.py` - Final response/output wrapper.
- `agents/template_loader.py` - Loads configured skeleton templates from `templates/`.
- `agents/__init__.py` - Package marker.

## `api/`

Minimal HTTP request boundary around the existing synchronous controller.

- `api/app.py` - FastAPI app with `/health` and `/runs/sync`.
- `api/__init__.py` - Package marker.

## `kernel/`

The kernel names the shared execution boundary. It wraps the stable controller
instead of replacing it.

- `kernel/task_ir.py` - `TaskIR`, `TemplateRoute`, and `ValidationPlan` dataclasses.
- `kernel/function_contracts.py` - Function contract queue, Deal examples, scaffold rendering, and worker packets.
- `kernel/execution_kernel.py` - Thin wrapper that delegates `TaskIR` execution to `GenerationController`.
- `kernel/__init__.py` - Public kernel exports.

## `engines/`

Static analysis engines. Python uses standard-library `ast`; C/C++ can use
optional tree-sitter support.

- `engines/base.py` - Shared engine finding and diagnostic types.
- `engines/decomposition_engine.py` - Python AST decomposition layer used by Python engines.
- `engines/math_engine.py` - Loop-depth analysis.
- `engines/hazards_engine.py` - Global/module-state mutation, dependency, unsafe call, and registered-library API checks.
- `engines/branching_engine.py` - Cyclomatic complexity and branch-density analysis.
- `engines/cost_engine.py` - Algorithmic-cost hotspot detection.
- `engines/bounds_engine.py` - Advisory out-of-bounds read/write pattern detection.
- `engines/state_flow_engine.py` - Lost parser/event state update detection.
- `engines/lint_engine.py` - Optional Pylint fatal/error gate.
- `engines/evaluator.py` - Engine recall evaluator against fixture labels.
- `engines/library_registry.py` - Trusted library API registry reader.
- `engines/treesitter_support.py` - Optional tree-sitter parser loader and cache.
- `engines/treesitter_engine.py` - C/C++ structural engines.
- `engines/__init__.py` - Package marker.

## `validation/`

Validation turns findings and runtime checks into pass/fail decisions.

- `validation/types.py` - `Violation`, `ValidationResult`, violation kinds, and repair hints.
- `validation/policy.py` - Maps engine findings to blocking or advisory violations.
- `validation/behavior.py` - Timeout-bound behavioral validation for generated Python.
- `validation/deal_contracts.py` - Executes explicit `@deal.example` contracts from generated code.
- `validation/formal.py` - Optional CrossHair semantic validation.
- `validation/finding_aggregator.py` - Coordination layer for grouped findings.
- `validation/branch_loop_detector.py` - Branch-state fingerprinting and no-progress cycle detection.
- `validation/__init__.py` - Package marker.

## `prompt/`

Prompt builders convert structured context and findings into model-facing text.

- `prompt/constraint_types.py` - Constraint-block dataclasses.
- `prompt/builder.py` - Initial structured prompt builder.
- `prompt/retry_builder.py` - Low-noise small-worker and richer retry-prompt builder.
- `prompt/architect_builder.py` - Architect-tier state-machine repair prompt builder.
- `prompt/contract_builder.py` - Architect-tier prompt builder for Deal-compatible function contracts.
- `prompt/__init__.py` - Package marker.

## `backends/`

Model integrations.

- `backends/ollama_client.py` - Local Ollama supplier and code extraction.
- `backends/architect_client.py` - API-backed architect client, split contract/repair profiles, contract queue supplier, `.env` loading, response cleanup, and formalization prompt helpers.
- `backends/__init__.py` - Package marker.

## `data/`

Ground truth cases, behavior specs, library schemas, run logs, and code snippets.

- `data/engine_cases.json` - Expected engine findings for Python fixtures.
- `data/behavior_cases.json` - Behavior specs for generated-function validation.
- `data/library_registry.json` - Trusted library API schemas and adapter notes.
- `data/library_proposals/` - Untrusted discovery proposals and model-generated library docs awaiting review.
- `data/runs.jsonl` - Optional append-only historian log.
- `data/stats.json` - Optional aggregate route statistics.
- `data/snippets/` - Python, C, and C++ static-analysis fixtures.

## `examples/`

External experiment inputs that are intentionally kept outside the harness
design and runtime logic.

- `examples/specs/` - Structured app/task specs for manual experiments.

## `scripts/`

Runnable experiments and operator tooling.

- `scripts/test_inference.py` - Ollama inference smoke test.
- `scripts/normalize_prompt.py` - Prompt-normalization CLI.
- `scripts/aggregate_history.py` - Builds aggregate route stats from raw run logs.
- `scripts/discover_library.py` - Writes a reviewable library proposal and can ask DeepSeek, Qwen, or Kernel for documentation.
- `scripts/approve_library.py` - Merges an approved proposal into the trusted registry.
- `scripts/run_adversarial_prompts.py` - Runs trap prompts through the PEV loop.
- `scripts/run_coding_capability.py` - Runs codegen tasks through engines, behavior checks, and optional architect escalation.
- `scripts/run_worker_limit.py` - Harder-and-harder worker ladder.
- `scripts/run_plan_mode_ladder.py` - Deterministic Plan Mode extraction ladder.
- `scripts/run_raw_vs_harness.py` - Raw model versus harness comparison.
- `scripts/run_formal_experiment.py` - Optional CrossHair smoke experiment.
- `scripts/review_run.py` - Human-readable artifact-run summary.
- `scripts/run_live_repair.py` - Live repair loop against a fixture.

## `tests/`

Unit and integration coverage.

- `tests/test_benchmarker.py` - Controller, prompt, behavior, supplier, and benchmark tests.
- `tests/test_agents_pipeline.py` - Agent, registry, repair, historian, library, and controller integration tests.
- `tests/test_behavior.py` - Behavior validator tests.
- `tests/test_engine_edge_cases.py` - Engine false-positive and boundary tests.
- `tests/test_cost_engine_scoping.py` - Cost-engine type/scoping tests.
- `tests/test_graph_grounded_context.py` - Graph/context preservation tests.
- `tests/test_context_window_prompt.py` - Compact prompt tests.
- `tests/test_architect_state_machine_prompt.py` - Architect prompt tests.
- `tests/test_branch_loop_detector.py` - Branch-loop fingerprint and progress tests.
- `tests/test_treesitter_pipeline.py` - Optional C/C++ tree-sitter tests.
- `tests/coding_capability/` - Code-generation task fixtures.
- `tests/worker_limit/` - Worker-limit ladder and decomposition fixtures.
- `tests/python_ladders/` - Focused Python ladders.
- `tests/plan_mode/` - Plan Mode ladder fixtures.
- `tests/adversarial/` - Trap prompt fixtures.

## Current Guarantees

- Parse failure blocks analysis.
- Valid Python drafts run through every registered Python engine.
- Repairs and architect output are rescanned by the same gates.
- Behavior validation can override static-clean output.
- Architect output is not accepted without validation.
- API failures become structured `manual_review_required` payloads.
- Artifact runs preserve prompts, drafts, diffs, findings, validation results, and attempt timelines.
