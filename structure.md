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

The inventory below names every file tracked by git. Runtime artifacts, virtual
environments, caches, `.env`, `data/jobs.jsonl`, and generated statistics are
described where relevant but are intentionally not tracked.

## Root Files

- `README.md` - Professional project overview, architecture, setup, and command surface.
- `Makefile` - Common setup, test, ladder, model, history, review, and smoke commands.
- `config.yaml` - Declarative policy, retry, model, behavior, and routing settings.
- `pyproject.toml` - Python package metadata and runtime dependencies.
- `Dockerfile` - Container entrypoint for the synchronous API service.
- `.dockerignore` - Excludes local secrets, caches, artifacts, and build output from image context.
- `.gitignore` - Keeps local environments, secrets, caches, run artifacts, and generated state out of commits.
- `.github/workflows/ci.yml` - GitHub Actions workflow for package install, tests, and Docker image build.
- `.env.example` - Safe template for local secrets such as architect API keys.
- `benchmarker.py` - Benchmark entrypoint and helper factory for Ollama-backed controllers.
- `requirements.txt` - Pip-compatible runtime dependency list, including tree-sitter, pygame, required Pylint, FastAPI, and Uvicorn.
- `requirements-kernel.txt` - Optional Kernel browser documentation dependency manifest.
- `requirements-formal.txt` - Optional Deal and CrossHair formal-validation dependency manifest.
- `history.json` - Historian persistence file for run summaries and successful repair lessons.
- `conventions.md` - Stable model-facing coding and harness rules.
- `context.md` - Current project context, decisions, and experiment notes.
- `design.md` - Architecture, safety, escalation, and validation design constraints.
- `structure.md` - This file.

## `agents/`

Deterministic orchestration components. These are not free-running autonomous
agents; they prepare, route, validate, and record work.

- `agents/base.py` - Shared `AgentResult` and `BaseAgent` types.
- `agents/artifact_manager.py` - Creates per-run artifact directories and records prompts, attempts, findings, diffs, token estimates, and timelines.
- `agents/config_loader.py` - Strict dataclass-backed `config.yaml` loader.
- `agents/preprocessor.py` - Loads context and convention files before generation.
- `agents/prompt_normalizer.py` - Removes conversational filler from raw prompts.
- `agents/task_classifier.py` - Infers task type, language, library hints, and route hints.
- `agents/plan_mode.py` - Extracts target functions, behavior examples, state rules, graph context, adapter contracts, Deal candidates, and `TaskIR`. Optionally merges repo-map context when a repo root is supplied.
- `agents/repo_map_agent.py` - AST repo walker that maps functions, variables, mutations, loop sites, and classified imports into typed `RepoGraph` nodes/edges, compact Plan Mode context, JSON, and live Mermaid output.
- `agents/template_registry.py` - Optional injected template-route selector. It has no built-in app-specific route.
- `agents/routing_policy.py` - Chooses worker, template-assisted worker, architect escalation, or manual review.
- `agents/coder.py` - Builds initial model prompts from context and behavior specs.
- `agents/parse_contract.py` - Language detection and parser gate.
- `agents/engine_registry.py` - Routes parseable source to the registered engine set.
- `agents/generation_controller.py` - Main loop for drafting, validation, repair, branch-loop detection, architect fallback, and final status. Optionally runs the execution agent after the contract parses and records the trace on each attempt.
- `agents/execution_agent.py` - Runs a parsed draft against its behavior examples in the isolated sandbox and returns an `ExecutionTrace` for the behavior gate and debugger hook.
- `agents/repair_strategy.py` - Turns validation failures into targeted repair directives.
- `agents/behavior_spec.py` - Loads behavior specs from `data/behavior_cases.json`.
- `agents/historian.py` - Records raw runs and aggregates route statistics.
- `agents/job_store.py` - File-locked append-only JSONL store for async job/status orchestration.
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

- `api/app.py` - FastAPI app with sync generation, async job submission/status, and backend configuration wiring.
- `api/__init__.py` - Package marker.

## `harness_kernel/`

The kernel names the shared execution boundary. It wraps the stable controller
instead of replacing it.

- `harness_kernel/task_ir.py` - `TaskIR`, `TemplateRoute`, and `ValidationPlan` dataclasses.
- `harness_kernel/function_contracts.py` - Function contract queue, Deal examples, scaffold rendering, and worker packets.
- `harness_kernel/execution_kernel.py` - Thin wrapper that delegates `TaskIR` execution to `GenerationController`.
- `harness_kernel/__init__.py` - Public harness-kernel exports; the name avoids collision with the Kernel browser SDK.

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
- `engines/lint_engine.py` - Required Pylint fatal/error gate that reports an explicit skipped-lint signal when execution is unavailable.
- `engines/evaluator.py` - Engine recall evaluator against fixture labels.
- `engines/library_registry.py` - Trusted library API registry reader.
- `engines/treesitter_support.py` - Optional tree-sitter parser loader and cache.
- `engines/treesitter_engine.py` - C/C++ structural engines.
- `engines/__init__.py` - Package marker.

## `validation/`

Validation turns findings and runtime checks into pass/fail decisions.

- `validation/types.py` - `Violation`, `ValidationResult`, violation kinds, and repair hints.
- `validation/policy.py` - Maps engine findings to blocking or advisory violations.
- `validation/behavior.py` - Timeout-bound behavioral validation whose sandbox emits bounded per-case returns, output, exceptions, tracebacks, and timing in an `ExecutionTrace`; `BehaviorResult` and runtime-backed issue details are derived from it.
- `validation/debugger.py` - Debugger-mode hook that diffs an `ExecutionTrace` against the spec sheet into bounded, targeted repair hints.
- `validation/deal_contracts.py` - Executes explicit `@deal.example` contracts from generated code.
- `validation/formal.py` - Optional CrossHair semantic validation.
- `validation/import_graph.py` - Checks local import resolution and verifies named symbols on allowed real modules before contract acceptance.
- `validation/finding_aggregator.py` - Coordination layer for grouped findings.
- `validation/branch_loop_detector.py` - Branch-state fingerprinting and no-progress cycle detection.
- `validation/__init__.py` - Package marker.

## `prompt/`

Prompt builders convert structured context and findings into model-facing text.

- `prompt/constraint_types.py` - Constraint-block dataclasses.
- `prompt/budget.py` - Estimates prompt size and truncates older context while preserving current failures, drafts, and final rules.
- `prompt/builder.py` - Initial structured prompt builder.
- `prompt/backend_failure_builder.py` - Converts model/backend exceptions into bounded, structured manual-review responses.
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
- `data/library_proposals/clang.cindex.json` - Untrusted discovered-symbol proposal for the `clang.cindex` package.
- `data/library_proposals/clang.cindex.docs.md` - Model-generated `clang.cindex` syntax and usage notes awaiting human review.
- `data/runs.jsonl` - Optional append-only historian log.
- `data/jobs.jsonl` - Local async job/status log; ignored by git.
- `data/stats.json` - Optional aggregate route statistics.
- `data/snippets/linear_safe.py` - Simple Python fixture expected to remain free of engine findings.
- `data/snippets/nested_loop.py` - Python fixture with nested loops for loop-depth analysis.
- `data/snippets/triple_nested.py` - Python fixture that exceeds the configured loop-depth policy.
- `data/snippets/branchy_but_safe.py` - Branch-heavy Python fixture near the accepted complexity boundary.
- `data/snippets/global_and_branch_heavy.py` - Combined global-state and branching violation fixture.
- `data/snippets/global_in_helper.py` - Python fixture for detecting global mutation inside a helper function.
- `data/snippets/mixed_hard_case.py` - Multi-violation Python fixture used to exercise finding aggregation.
- `data/snippets/c/simple.c` - Minimal safe C fixture for the optional tree-sitter pipeline.
- `data/snippets/c/nested_branchy.c` - C branching-depth fixture.
- `data/snippets/c/unsafe.c` - C fixture containing unsafe operations that should be reported.
- `data/snippets/cpp/simple.cpp` - Minimal safe C++ fixture for tree-sitter parsing.
- `data/snippets/cpp/nested_branchy.cpp` - C++ branching-depth fixture.

## `docs/`

Checked-in evaluation evidence and review material.

- `docs/qwen-capability-results-2026-07-18.md` - Qwen coding-capability baseline, retry, and architect-escalation results.
- `docs/gemma-deepseek-capability-results-2026-07-19.md` - Gemma worker and DeepSeek architect comparison results.
- `docs/snake-pong-execution-report-2026-07-19.md` - Reproducible Snake/Pong commands, failures, fixes, artifact IDs, and post-fix runtime outcomes.
- `docs/open_source_readiness_audit.pdf` - Snapshot of the repository's open-source readiness review.

## `examples/`

External experiment inputs that are intentionally kept outside the harness
design and runtime logic.

- `examples/specs/snake_game_spec.md` - External multi-contract Snake application stress specification.
- `examples/specs/pong_game_spec.md` - External multi-contract Pong application stress specification.

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
- `scripts/run_repo_map.py` - Runs the repo mapper and prints the compact context, JSON graph, or mermaid diagram (optionally saving artifacts).
- `scripts/run_structured_spec.py` - Plans a structured specification into a contract queue, generates and validates each contract, assembles the program, injects accepted interface context, runs the integration smoke gate, and saves artifacts.

## `tests/`

Unit and integration coverage.

- `tests/test_benchmarker.py` - Controller, prompt, behavior, supplier, and benchmark tests.
- `tests/test_api.py` - Synchronous and asynchronous FastAPI boundary and job-store tests.
- `tests/test_agents_pipeline.py` - Agent, registry, repair, historian, library, and controller integration tests.
- `tests/test_behavior.py` - Behavior validator parity, isolation, timeout, trace, output-capture, exception, and runtime-backed issue tests.
- `tests/test_engine_edge_cases.py` - Engine false-positive and boundary tests.
- `tests/test_cost_engine_scoping.py` - Cost-engine type/scoping tests.
- `tests/test_graph_grounded_context.py` - Graph/context preservation tests.
- `tests/test_context_window_prompt.py` - Compact prompt tests.
- `tests/test_architect_state_machine_prompt.py` - Architect prompt tests.
- `tests/test_branch_loop_detector.py` - Branch-loop fingerprint and progress tests.
- `tests/test_treesitter_pipeline.py` - Optional C/C++ tree-sitter tests.
- `tests/test_deal_contract_queue.py` - Deal example extraction, contract queue, scaffold, and worker-packet tests.
- `tests/test_structured_spec_runner.py` - Structured-spec parsing, contract validation, imported-symbol checks, accepted field/method context, artifact output, and smoke-execution regressions.
- `tests/test_repo_map_agent.py` - Repo mapper record/node/edge extraction, call/import/mutation graphing, import classification, unparseable-file skip, renderings, and opt-in Plan Mode merge.
- `tests/test_execution_agent.py` - Execution-trace capture, parse-success controller attachment, default-off behavior, and debugger type-contract hook tests.
- `tests/coding_capability/tasks.json` - Seven code-generation tasks with executable expected behaviors and edge cases.
- `tests/worker_limit/tasks.json` - Graduated task set used to locate the local worker's capability boundary.
- `tests/worker_limit/decompositions.json` - Decomposed versions of worker-limit tasks for testing whether smaller contracts improve completion.
- `tests/python_ladders/algorithmic.json` - Graduated algorithmic-reasoning cases.
- `tests/python_ladders/data_transform.json` - Graduated collection and data-transformation cases.
- `tests/python_ladders/parsing.json` - Graduated text-parsing and state-tracking cases.
- `tests/python_ladders/stateful.json` - Graduated mutable-state and transition cases.
- `tests/plan_mode/tasks.json` - Deterministic prompts and expected Plan Mode extraction fields.
- `tests/adversarial/prompts.json` - Trap prompts used to verify that unsafe or misleading requests do not bypass validation.

## Current Guarantees

- Parse failure blocks analysis.
- Valid Python drafts run through every registered Python engine.
- Repairs and architect output are rescanned by the same gates.
- Behavior validation can override static-clean output.
- Architect output is not accepted without validation.
- Missing imported symbols on real allowed modules block contract acceptance.
- Accepted class field types and binding method arities are carried into downstream contract prompts.
- A skipped required Pylint run blocks completion unless policy explicitly allows lint skips.
- Assembled interactive Python programs must survive the bounded headless integration smoke window.
- API failures become structured `manual_review_required` payloads.
- Artifact runs preserve prompts, drafts, diffs, findings, validation results, and attempt timelines.
- The repo mapper builds task-agnostic structural context from real files and is re-run per task rather than cached, so it stays fresh as the worker edits files.
- When execution tracing is enabled, behavior pass/fail is derived from a real run captured as an `ExecutionTrace`; the trace is evidence for the existing behavior gate and the debugger hook, not a new blocking gate.
