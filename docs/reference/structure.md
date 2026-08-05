# Repository Structure

> Tree audit: 2026-07-30. Generated caches, build output, local environments,
> and run artifacts are intentionally omitted.

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
  -> static engines, including strict C/C++ compilation when applicable
  -> policy and behavior validation
  -> optional algorithmic profiling and formal validation
  -> checkpoint current attempt and next-draft state
  -> repair, evidence-backed completion recommendation, or manual review
  -> human acceptance / rejection outside the harness
```

The harness should stay task-agnostic. Domain-specific examples belong in test
fixtures or external experiment specs, not in the controller, Plan Mode, or
engine logic. Its output supports human review; it does not independently
authorize merges, deployment, or product acceptance.

The inventory below names every file tracked by git. Runtime artifacts, virtual
environments, caches, `.env`, `data/jobs.jsonl`, and generated statistics are
described where relevant but are intentionally not tracked.

## Root Files

- `README.md` - Professional project overview, architecture, setup, and command surface.
- `docs/reference/SPEC.md` - Rust TUI and engine-expansion specification plus current implementation status and rollout constraints.
- `rust/Cargo.toml` - Pinned Rust TUI dependencies.
- `Makefile` - Common setup, Rust/Textual TUI, test, ladder, model, history, review, Compute Shield, and smoke commands.
- `config.yaml` - Declarative policy, retry, model, behavior, and routing settings.
- `pyproject.toml` - Python package metadata and runtime dependencies.
- `Dockerfile` - Container entrypoint for the synchronous API service.
- `install.sh` - Local clone/update, virtualenv setup, optional key setup, and Rust TUI build.
- `.dockerignore` - Excludes local secrets, caches, artifacts, and build output from image context.
- `.gitignore` - Keeps local environments, secrets, caches, run artifacts, and generated state out of commits.
- `.github/workflows/ci.yml` - GitHub Actions workflow for package install, tests, and Docker image build.
- `.env.example` - Safe template for local secrets such as architect API keys.
- `benchmarker.py` - Benchmark entrypoint and helper factory for Ollama-backed controllers.
- `requirements.txt` - Pip-compatible runtime dependency list, including tree-sitter, pygame, required Pylint, FastAPI, Uvicorn, and Textual.
- `requirements-kernel.txt` - Optional Kernel browser documentation dependency manifest.
- `requirements-formal.txt` - Optional Deal and CrossHair formal-validation dependency manifest.
- `history.json` - Historian persistence file for run summaries and successful repair lessons.
- `docs/reference/conventions.md` - Stable model-facing coding and harness rules.
- `docs/reference/design.md` - Architecture, safety, escalation, and validation design constraints.
- `docs/reference/structure.md` - This file.

## `docs/`

- `docs/reference/README.md` - Index for the maintained project reference documents.
- `docs/reference/SPEC.md` - Implemented Rust TUI and engine-expansion specification.
- `docs/*-2026-*.md` - Dated benchmark and experiment reports retained as historical evidence.
- `docs/open_source_readiness_audit.pdf` - Historical readiness audit.

## `agents/`

Deterministic orchestration components. These are not free-running autonomous
agents; they prepare, route, validate, and record work.

- `agents/base.py` - Shared `AgentResult` and `BaseAgent` types.
- `agents/artifact_manager.py` - Creates per-run artifact directories, atomically checkpoints resumable controller state, enumerates/reloads checkpoints by run ID, and records final prompts, attempts, execution traces, profiling evidence, findings, diffs, token estimates, and timelines.
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
- `agents/engine_registry.py` - Routes parseable source to the registered engine set and dispatches lint through the typed tool registry when one is configured.
- `agents/generation_controller.py` - Main loop for drafting, validation, repair, branch-loop detection, architect fallback, resumable checkpoint state, bounded advisory history injection, prompt summarization, and final status. It runs behavior execution after parsing, emits compilation events, invokes an optional profiling runner, records trace/profile evidence per attempt, and dispatches formal checks through the typed tool registry.
- `agents/execution_agent.py` - Runs a parsed draft against its behavior examples in the sanitized, disposable local subprocess boundary and returns an `ExecutionTrace` for the behavior gate and debugger hook.
- `agents/repair_strategy.py` - Turns validation failures into targeted repair directives.
- `agents/behavior_spec.py` - Loads behavior specs from `data/behavior_cases.json`.
- `agents/historian.py` - Records raw runs, aggregates route statistics, and retrieves a bounded set of lexically similar past attempts for optional advisory prompt context.
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

## `TUI/`

Separate Textual process for launching and reviewing harness work. It shells out
to existing CLI entrypoints and consumes JSON artifacts rather than importing
the controller.

- `TUI/app.py` - Run launcher, live static/behavior/profiling/formal attempt dashboard, contract dashboard, architecture and changes modals, history screen, hotkeys, and exact runtime-repair safety copy.
- `TUI/data_source.py` - Single JSON/subprocess boundary for run enumeration, checkpoint loading, CLI launch/resume, bounded history lookup, repo mapping, Mermaid SVG handoff, and unified attempt diffs.
- `TUI/mermaid_renderer.py` - Human-scale layer/dependency summary plus a bounded parser/ASCII-tree renderer for the repo mapper's low-level Mermaid output.
- `TUI/CODE_SPEC.md` - Grounded Phase 1/2 implementation specification and Phase 3 exclusions.
- `TUI/__main__.py` - `python -m TUI` entrypoint.
- `TUI/__init__.py` - Public data-source types.

## `harness_kernel/`

The kernel names the shared execution boundary. It wraps the stable controller
instead of replacing it.

- `harness_kernel/task_ir.py` - `TaskIR`, `TemplateRoute`, and `ValidationPlan` dataclasses.
- `harness_kernel/function_contracts.py` - Function contract queue, Deal examples, scaffold rendering, and worker packets.
- `harness_kernel/execution_kernel.py` - Thin wrapper that delegates `TaskIR` execution to `GenerationController`.
- `harness_kernel/tool_registry.py` - Typed named-tool dispatch boundary with uniform success and failure results.
- `harness_kernel/tool_handlers.py` - Typed lint, execution-sandbox, Ollama-generation, architect-generation, and Deal/CrossHair formal-verification handlers wrapping the existing implementations.
- `harness_kernel/local_sandbox.py` - Sanitized, disposable, resource-limited local Python subprocess boundary used by generated-code validation and smoke execution; explicitly not a replacement for container/OS isolation.
- `harness_kernel/tui_bridge.py` - Versioned JSON-lines subprocess bridge used by the Rust TUI; owns questionnaire normalization, chat/spec model calls, allowlisted CLI launches, and typed repo-map, compilation, profiling, and Compute Shield events.
- `harness_kernel/event_stream.py` - Optional inherited file-descriptor event sink that keeps controller JSON events separate from human-readable CLI stdout.
- `harness_kernel/profiling.py` - Opt-in repeated behavioral profiler with median/spread evidence, optional cache counters, a noise floor, and slower-selection findings.
- `harness_kernel/compute_shield.py` - Exact per-task and aggregate baseline-versus-shielded token accounting; evaluation evidence rather than a validation gate.
- `harness_kernel/__init__.py` - Public harness-kernel exports; the name avoids collision with the Kernel browser SDK.

## `rust/`

- `rust/Cargo.toml` - Pinned Rust application dependencies for Ratatui, Tokio, terminal image protocols, Mermaid SVG rendering, and rasterization.
- `rust/src/main.rs` - Async terminal application, Python bridge lifecycle, pure application-state reducer, input/event/redraw selection loop, and review dashboard.
- `rust/src/protocol.rs` - Serde command/event contract and resilient JSON-lines reader.
- `rust/src/mermaid_view.rs` - In-process Mermaid SVG rendering, PNG rasterization, terminal protocol selection, and modal widget.

The Rust client is additive during rollout. Python remains the source of truth
for engine logic and artifacts, and the Textual TUI remains available until
terminal compatibility and feature parity are manually confirmed.

## Additional engine

- `engines/compilation_engine.py` - Strict, bounded Clang/GCC syntax and warning gate for C/C++; registered before optional tree-sitter structural engines.

## New evaluation command

- `scripts/run_compute_shield.py` - Reads paired `ArtifactManager` metadata,
  verifies matching task names, aggregates exact recorded model-token totals,
  and prints/saves the typed phase-three Compute Shield event without rerunning
  a model.

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
- `prompt/budget.py` - Estimates prompt size, optionally summarizes older context while preserving the latest diagnostics verbatim, and falls back to deterministic tail truncation.
- `prompt/summarizer.py` - Default deterministic extractive compressor for older failed-attempt history; it never rewrites the current diagnostic section.
- `prompt/builder.py` - Initial structured prompt builder.
- `prompt/backend_failure_builder.py` - Converts model/backend exceptions into bounded, structured manual-review responses.
- `prompt/retry_builder.py` - Low-noise small-worker and richer retry-prompt builder.
- `prompt/architect_builder.py` - Architect-tier state-machine repair prompt builder.
- `prompt/contract_builder.py` - Architect-tier prompt builder for Deal-compatible function contracts.
- `prompt/__init__.py` - Package marker.

## `backends/`

Model integrations.

- `backends/ollama_client.py` - Local Ollama supplier, typed tool dispatch, and code extraction.
- `backends/architect_client.py` - API-backed architect client, typed tool dispatch, split contract/repair profiles, contract queue supplier, `.env` loading, response cleanup, and formalization prompt helpers.
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
- `docs/additional-harness-results-2026-07-24.md` - Raw-versus-harness, repeated paired-sample, structured-spec resume, Pong closure, and stateful-ladder architect-recovery results.
- `docs/structured-spec-repo-map-results-2026-07-24.md` - Structured-spec Snake/Pong plan and full-run results plus repo-mapper context/Mermaid/JSON output metrics.
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
- `scripts/run_coding_capability.py` - Runs codegen tasks through engines, behavior checks, and optional architect escalation; `--resume-run` reloads a saved matching task checkpoint.
- `scripts/run_worker_limit.py` - Harder-and-harder worker ladder with checkpoint resume through `--resume-run`.
- `scripts/run_plan_mode_ladder.py` - Deterministic Plan Mode extraction ladder.
- `scripts/run_raw_vs_harness.py` - Raw model versus harness comparison with an opt-in one-repair naive ablation, bounded repair/architect mode, repeated paired samples, retained drafts, variance, Wilson intervals, and aggregate recovery metrics.
- `scripts/run_formal_experiment.py` - Optional CrossHair smoke experiment.
- `scripts/review_run.py` - Human-readable artifact-run summary.
- `scripts/run_live_repair.py` - Live repair loop against a fixture.
- `scripts/run_repo_map.py` - Runs the repo mapper and prints the compact context, JSON graph, or mermaid diagram (optionally saving artifacts).
- `scripts/run_structured_spec.py` - Plans a structured specification into a contract queue, checkpoints terminal contract results, resumes without regenerating checkpointed contracts, assembles the program, injects accepted interface context, runs the integration smoke gate, and saves artifacts.

## `tests/`

Unit and integration coverage.

- `tests/test_benchmarker.py` - Controller, prompt, behavior, supplier, and benchmark tests.
- `tests/test_checkpoint_resume.py` - Atomic checkpoint persistence plus controller, CLI-runner, and structured-spec resume surface regressions.
- `tests/test_prompt_summarizer.py` - Default retry-history compression and verbatim live-diagnostic preservation tests.
- `tests/test_tool_registry.py` - Typed tool dispatch, backend failure containment, and default-handler tests.
- `tests/test_raw_vs_harness.py` - Repair-enabled raw comparison wiring, naive-ablation isolation, repeated paired artifact retention, aggregate statistics, and architect metric coverage.
- `tests/test_lint_engine.py` - Task-agnostic wildcard-import blocking and explicit-import acceptance.
- `tests/test_api.py` - Synchronous and asynchronous FastAPI boundary and job-store tests.
- `tests/test_agents_pipeline.py` - Agent, registry, repair, historian, library, and controller integration tests.
- `tests/test_behavior.py` - Behavior validator parity, isolation, timeout, trace, output-capture, exception, and runtime-backed issue tests.
- `tests/test_engine_edge_cases.py` - Engine false-positive and boundary tests.
- `tests/test_engine_expansion.py` - Compilation-engine C/C++ gate, algorithmic-profiler selection/noise-floor, and Compute Shield token-accounting tests.
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
- `tests/test_tui.py` - Run enumeration, command allowlisting, resume inference, repo-map output, attempt/contract diffs, status-copy correctness, and headless Textual mount tests.
- `tests/test_tui_bridge.py` - JSON-lines TUI bridge command dispatch, argument allowlisting, typed profiling/Compute Shield/repo-map events, and inherited-fd event-sink tests.
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
