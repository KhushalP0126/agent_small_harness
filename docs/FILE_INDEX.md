# Repository File Index

This is the path-to-purpose companion to the [README](../README.md). It
indexes the maintained repository surface without treating generated caches,
private `.env` files, or untracked local experiments as project files.

`__init__.py` files only mark their directories as Python packages unless a
row says otherwise. Result Markdown files are the human-readable counterpart
to same-named JSON under `docs/results/raw/`.

## Root and setup

| Path | Purpose |
| --- | --- |
| `.dockerignore` | Keeps local caches, artifacts, and credentials out of container builds. |
| `.env.example` | Safe environment-variable template for local/API configuration. |
| `.github/workflows/ci.yml` | Continuous-integration checks for Python and Rust. |
| `.gitignore` | Excludes generated, local, and secret files from version control. |
| `README.md` | Project overview, setup, capabilities, and repository guide. |
| `ARCHITECTURE.md` | Task-oriented map of how the major runtime components fit together. |
| `REPORT.md` | High-level research and benchmark summary. |
| `CHANGELOG.md` | Curated record of user-visible project changes. |
| `Makefile` | Supported setup, test, TUI, benchmark, and reporting commands. |
| `config.yaml` | Default model, backend, and harness configuration. |
| `pyproject.toml` | Python project metadata and tool configuration. |
| `requirements.txt` | Core Python runtime dependencies. |
| `requirements-formal.txt` | Optional formal-verification dependencies. |
| `requirements-kernel.txt` | Optional kernel/documentation-search dependencies. |
| `Dockerfile` | Reproducible container image for isolated execution. |
| `benchmarker.py` | Legacy/simple benchmark helper retained for compatibility. |
| `history.json` | Legacy history data; current run evidence lives in artifacts/results. |
| `setup/README.md` | Detailed setup notes and troubleshooting. |

## Runtime clients and orchestration

| Path | Purpose |
| --- | --- |
| `api/app.py` | Optional FastAPI endpoint for runs, jobs, and health status. |
| `backends/architect_client.py` | DeepSeek-compatible architect client, retries, thinking settings, and usage telemetry. |
| `backends/ollama_client.py` | Local Ollama client and local-model token/context telemetry. |
| `agents/artifact_manager.py` | Writes run artifacts, checkpoints, summaries, and timelines. |
| `agents/attempt_analysis.py` | Pure draft-diff and diagnostic-stagnation helpers used between repair attempts. |
| `agents/base.py` | Shared agent interfaces and core data types. |
| `agents/behavior_spec.py` | Converts requirements into executable behavior examples. |
| `agents/coder.py` | Local/remote code-draft generation abstraction. |
| `agents/config_loader.py` | Loads harness configuration and environment overrides. |
| `agents/dependency.py` | Dependency ordering utilities for contract queues. |
| `agents/engine_registry.py` | Selects validation engines by supported language. |
| `agents/execution_agent.py` | Runs generated candidates through the execution kernel. |
| `agents/generation_controller.py` | Main create/validate/repair/escalate controller. |
| `agents/historian.py` | Records run history and computes route success/cost telemetry. |
| `agents/job_store.py` | Persists asynchronous API job state. |
| `agents/kernel_doc_search.py` | Searches kernel/documentation context for coding tasks. |
| `agents/library_discovery.py` | Identifies candidate third-party libraries for a task. |
| `agents/library_doc_search.py` | Retrieves library documentation for registered/discovered libraries. |
| `agents/parse_contract.py` | Parses drafted source into language-aware contract information. |
| `agents/plan_mode.py` | Builds structured plans, questionnaires, and contract queues. |
| `agents/postprocessor.py` | Normalizes generated output after model generation. |
| `agents/preprocessor.py` | Normalizes task input before planning/generation. |
| `agents/prompt_normalizer.py` | Cleans and classifies free-form user prompts. |
| `agents/repair_strategy.py` | Selects bounded repair behavior from findings and attempts. |
| `agents/repo_map_agent.py` | Builds typed repository structure, symbol, and import maps. |
| `agents/routing_policy.py` | Chooses measured routes using success, token, and cost history. |
| `agents/scope_tracker.py` | Tracks task scope and prevents uncontrolled expansion. |
| `agents/task_classifier.py` | Separates chat, planning, repository, and coding work. |
| `agents/template_loader.py` | Loads prompt/template assets. |
| `agents/template_registry.py` | Registers reusable generation and repair templates. |
| `agents/tool_calling_agent.py` | Bounded multi-turn agent for repository inspection and approved edits. |
| `agents/README.md` | Notes for the orchestration package. |

## Validation and deterministic engines

| Path | Purpose |
| --- | --- |
| `engines/base.py` | Common engine interface and finding contract. |
| `engines/bounds_engine.py` | Detects likely array/index bounds errors. |
| `engines/branching_engine.py` | Measures branching and cyclomatic complexity. |
| `engines/compilation_engine.py` | Runs language compilation/syntax gates for C, C++, Rust, and JavaScript. |
| `engines/cost_engine.py` | Flags avoidable algorithmic cost patterns. |
| `engines/decomposition_engine.py` | Extracts structural IR from supported source languages. |
| `engines/evaluator.py` | Aggregates engine results into a validation decision. |
| `engines/hazards_engine.py` | Detects unsafe calls, unknown APIs, and risky imports. |
| `engines/import_extractors.py` | Extracts imports across Python, C/C++, Rust, and JavaScript. |
| `engines/import_risk.py` | Maps imports/calls to declared risk categories. |
| `engines/library_registry.py` | Loads approved library surfaces and abstraction stubs. |
| `engines/lint_engine.py` | Runs required lint checks and bounds diagnostics. |
| `engines/math_engine.py` | Detects nesting and growth-risk patterns. |
| `engines/state_flow_engine.py` | Detects state updates that are not returned or propagated. |
| `engines/treesitter_engine.py` | Runs optional tree-sitter structural analysis. |
| `engines/treesitter_support.py` | Language/parser availability helpers for tree-sitter. |
| `engines/README.md` | Engine package documentation. |
| `validation/behavior.py` | Isolated behavior-case execution and result comparison. |
| `validation/branch_loop_detector.py` | Detects repeated repair branches and stagnation. |
| `validation/deal_contracts.py` | Optional Deal-contract checks. |
| `validation/debugger.py` | Turns failing traces into bounded debugging hints. |
| `validation/finding_aggregator.py` | Combines findings from engines and validators. |
| `validation/formal.py` | Optional CrossHair/Z3 formal verification and counterexample extraction. |
| `validation/formal_repair_router.py` | Matches known formal failure modes to tested repair directives. |
| `validation/import_graph.py` | Validates imports and imported symbols across project files. |
| `validation/policy.py` | Applies acceptance/manual-review policy to findings. |
| `validation/types.py` | Shared validation models. |

## Harness kernel, prompts, and repository tools

| Path | Purpose |
| --- | --- |
| `harness_kernel/compute_shield.py` | Measures paired shielded-vs-baseline model contribution. |
| `harness_kernel/container_sandbox.py` | Runs untrusted code with Docker/Podman isolation policy. |
| `harness_kernel/e2e_benchmark.py` | Defines end-to-end benchmark tasks and repeatable runners. |
| `harness_kernel/event_stream.py` | Typed event transport between workers and the TUI. |
| `harness_kernel/execution_kernel.py` | Coordinates isolated candidate execution. |
| `harness_kernel/function_contracts.py` | Parses and validates callable/function contracts. |
| `harness_kernel/language_adapters.py` | Normalizes language-specific build/run behavior. |
| `harness_kernel/live_session.py` | Captures approval-reviewed live-session receipts. |
| `harness_kernel/local_sandbox.py` | Temp-directory, sanitized-environment Python execution. |
| `harness_kernel/task_graph.py` | Defines immutable task graphs, revisions, hashes, and DAG/path validation. |
| `harness_kernel/roles.py` | Registers typed planner, researcher, implementer, validator, and conflict-repair roles. |
| `harness_kernel/governance.py` | Central permission and capability evaluation for graph and tool actions. |
| `harness_kernel/orchestration.py` | Compiles legacy task requests and exposes orchestration service operations. |
| `harness_kernel/orchestration_runtime.py` | Runs approved nodes with deterministic scheduling, isolation, retry, and pause control. |
| `harness_kernel/orchestration_store.py` | Persists graph revisions and recoverable session state. |
| `harness_kernel/event_journal.py` | Writes sanitized append-only events and content-addressed artifacts for replay. |
| `harness_kernel/merge_queue.py` | Serializes reviewed editing proposals and rejects stale or overlapping changes. |
| `harness_kernel/checkpoints.py` | Captures repository checkpoints used by reviewed merges and rewind. |
| `harness_kernel/project_validation.py` | Runs trusted, capability-aware project validation across the five language profiles. |
| `harness_kernel/provider_settings.py` | Validates provider configuration without persisting raw credentials. |
| `harness_kernel/profiling.py` | Optional repeated behavioral performance profiling. |
| `harness_kernel/provenance.py` | Captures revision, OS, model, and experiment metadata. |
| `harness_kernel/research_reporting.py` | Computes benchmark summaries, intervals, and research tables. |
| `harness_kernel/task_ir.py` | Language-neutral structured task representation. |
| `harness_kernel/tool_handlers.py` | Read/search/create/move/edit tool implementations. |
| `harness_kernel/tool_paths.py` | Repository-root path safety checks. |
| `harness_kernel/tool_registry.py` | Typed registry/dispatch for repository tools. |
| `harness_kernel/tui_bridge.py` | JSONL bridge capabilities between the Rust client and Python harness. |
| `harness_kernel/terminal_bridge/commands.py` | Readable command-to-capability dispatch table for the bridge. |
| `prompt/architect_builder.py` | Builds architect-model planning prompts. |
| `prompt/backend_failure_builder.py` | Formats backend/API failure context for recovery. |
| `prompt/budget.py` | Enforces prompt/context budgets. |
| `prompt/builder.py` | Builds worker generation prompts. |
| `prompt/constraint_types.py` | Types for prompt constraints and task context. |
| `prompt/contract_builder.py` | Builds per-contract generation prompts. |
| `prompt/retry_builder.py` | Builds repair prompts from findings and counterexamples. |
| `prompt/summarizer.py` | Compacts long history and tool output. |
| `routing/bridge.py` | Public adapter for bridge-facing routing operations. |
| `routing/tools.py` | Public adapter for repository-tool routing operations. |
| `routing/README.md` | Routing package notes. |

## Interfaces

| Path | Purpose |
| --- | --- |
| `rust_tui/Cargo.toml` | Rust TUI package manifest and dependencies. |
| `rust_tui/Cargo.lock` | Locked Rust dependency versions. |
| `rust_tui/src/main.rs` | Ratatui event loop, conversation display, approvals, and state. |
| `rust_tui/src/protocol.rs` | Rust representation of JSONL bridge commands/events. |
| `rust_tui/src/render_support.rs` | Terminal theme, pane styling, and lightweight Markdown rendering. |
| `rust_tui/README.md` | Rust-client usage and terminal notes. |
| `TUI/app.py` | Legacy Textual artifact-review application. |
| `TUI/data_source.py` | Data access for the legacy Textual interface. |
| `TUI/repo_renderer.py` | Bounded terminal-native repository layer and tree rendering. |
| `TUI/CODE_SPEC.md` | Historical Textual UI specification. |
| `examples/specs/snake_game_spec.md` | Structured-spec smoke fixture for a Snake game. |
| `examples/specs/pong_game_spec.md` | Structured-spec smoke fixture for a Pong game. |

## Commands and experiments

| Path | Purpose |
| --- | --- |
| `scripts/aggregate_history.py` | Aggregates persisted run history. |
| `scripts/approve_library.py` | Reviews and approves a discovered library proposal. |
| `scripts/discover_library.py` | Searches for usable library candidates. |
| `scripts/normalize_prompt.py` | Normalizes a prompt through the project pipeline. |
| `scripts/record_live_session.py` | Records a controlled approval-reviewed session receipt. |
| `scripts/render_local_model_comparison.py` | Renders Qwen/local-model comparison results. |
| `scripts/render_research_report.py` | Builds a dated Markdown report from raw benchmark JSON. |
| `scripts/report_routing_stats.py` | Displays measured route success/cost statistics. |
| `scripts/review_run.py` | Opens/summarizes a persisted run for review. |
| `scripts/run_adversarial_prompts.py` | Exercises policy against adversarial prompt cases. |
| `scripts/run_agent_benchmark.py` | Runs the general agent benchmark corpus. |
| `scripts/run_coding_capability.py` | Runs coding-capability evaluation tasks. |
| `scripts/run_compute_shield.py` | Runs one Compute Shield measurement. |
| `scripts/run_compute_shield_experiment.py` | Runs the fixed Compute Shield experiment corpus. |
| `scripts/run_deepseek_benchmark_agent.py` | Runs a DeepSeek-backed benchmark arm. |
| `scripts/run_formal_experiment.py` | Runs formal-verification experiment variants. |
| `scripts/run_formal_repair_benchmark_agent.py` | Compares no-repair and counterexample-guided repair. |
| `scripts/run_formal_routed_repair_benchmark.py` | Runs the three-arm failure-mode-routed repair study. |
| `scripts/run_live_repair.py` | Runs an interactive repair flow from a saved task. |
| `scripts/run_ollama_benchmark_agent.py` | Runs a local-Ollama benchmark arm. |
| `scripts/run_plan_mode_ladder.py` | Evaluates plan-mode decomposition limits. |
| `scripts/run_raw_vs_harness.py` | Compares raw generation with harness-guided generation. |
| `scripts/run_repeated_agent_benchmark.py` | Repeats paired benchmarks and writes confidence-ready raw data. |
| `scripts/run_repo_map.py` | Produces a repository map artifact. |
| `scripts/run_sandbox.py` | Demonstrates isolated sandbox execution. |
| `scripts/run_structured_spec.py` | Runs structured-spec planning and optional implementation. |
| `scripts/run_tool_agent.py` | Runs the repository tool-calling agent. |
| `scripts/run_worker_limit.py` | Measures task decomposition against worker limits. |
| `scripts/test_inference.py` | Performs a small backend inference smoke test. |

## Versioned data and evidence

| Path or file family | Purpose |
| --- | --- |
| `data/agent_benchmark_tasks.json` | Main 20-task repository-agent benchmark corpus. |
| `data/behavior_cases.json` | General behavior-validation cases. |
| `data/compute_shield_tasks_10.json` | Frozen ten-task Compute Shield corpus. |
| `data/engine_cases.json` | Deterministic engine-test cases. |
| `data/formal_*_benchmark_tasks.json` | Formal-repair corpora, from focused to diverse task shapes. |
| `data/import_risk_categories.json` | Import and API risk taxonomy. |
| `data/library_registry.json` | Approved Python/Rust/JavaScript library surface registry. |
| `data/library_proposals/clang.cindex.json` | Candidate Clang C-index library proposal metadata. |
| `data/library_proposals/clang.cindex.docs.md` | Documentation snapshot for that proposal. |
| `data/research_fixture_tasks.json` | Independent repository fixture benchmark tasks. |
| `data/runs.jsonl` | Append-only historical run records. |
| `data/snippets/**/*.py` | Python engine/import-risk fixtures. |
| `data/snippets/c/*.c` and `data/snippets/cpp/*.cpp` | C/C++ compiler, parsing, and hazard fixtures. |
| `data/snippets/rust/imports.rs` | Rust import-extraction fixture. |
| `data/snippets/javascript/imports.js` | JavaScript import-extraction fixture. |
| `docs/RESEARCH.md` | Research questions, methodology, findings, and remaining work. |
| `docs/WORKSTREAMS.md` | Workstream status and ownership map. |
| `docs/open_source_readiness_audit.pdf` | External/open-source readiness audit record. |
| `docs/reference/*.md` | Historical design, structure, conventions, and specification references. |
| `docs/results/*.md` | Dated, human-readable benchmark and experiment reports. |
| `docs/results/raw/*.json` | Raw machine-readable inputs paired with the dated reports. |
| `docs/results/README.md` | Result-directory format and provenance rules. |

## Tests and fixtures

| Path or file family | Purpose |
| --- | --- |
| `tests/test_agents_pipeline.py` | Controller, language, retry, and orchestrator integration tests. |
| `tests/test_api.py` | FastAPI contract tests. |
| `tests/test_behavior.py`, `tests/test_execution_agent.py` | Behavior sandbox and execution-trace tests. |
| `tests/test_benchmarker.py`, `tests/test_e2e_benchmark.py` | Benchmark metrics, experiments, and repeat-run tests. |
| `tests/test_container_sandbox*.py`, `tests/test_local_sandbox.py` | Isolation and execution-policy tests. |
| `tests/test_engine_*.py`, `tests/test_lint_engine.py`, `tests/test_treesitter_pipeline.py` | Static-engine, compiler, lint, and structural-analysis coverage. |
| `tests/test_import_coverage.py` | Cross-language import extraction and symbol-validation tests. |
| `tests/test_research_provenance.py`, `tests/test_research_reporting.py` | Reproducible experiment metadata and report tests. |
| `tests/test_tool_*.py` | Tool registry, agent, safety, and approval-path tests. |
| `tests/test_tui.py`, `tests/test_tui_bridge.py` | Rust/Python terminal protocol and UI behavior tests. |
| Remaining `tests/test_*.py` files | Focused regression coverage for checkpoints, plan mode, prompts, repo maps, policy, and repair limits. |
| `tests/adversarial/prompts.json` | Adversarial-policy prompt fixture set. |
| `tests/coding_capability/tasks.json`, `tests/plan_mode/tasks.json` | Capability and plan-mode benchmark inputs. |
| `tests/python_ladders/*.json`, `tests/worker_limit/*.json` | Complexity/decomposition ladder fixtures. |
| `tests/fixtures/research_target_repo/*` | Small independent repository used for integration experiments. |

When adding a file, place it in the matching directory and update this index
when its responsibility creates a new maintained surface.
