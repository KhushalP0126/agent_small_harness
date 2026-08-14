# Everyday commands are intentionally small. Run `make help` first.
.DEFAULT_GOAL := help

VENV_PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PYTHON ?= $(VENV_PYTHON)
CARGO ?= cargo
RUST_MANIFEST := rust_tui/Cargo.toml
RUNS_PATH ?= data/runs.jsonl
CONFIG_PATH ?= config.yaml
ARTIFACT_ROOT ?= artifacts/runs
SAVE_ARTIFACTS ?= 1
MODEL ?= qwen2.5-coder:1.5b
MAX_RETRIES ?= 3
RUN ?=
RESUME_RUN ?=
ARCHITECT_AFTER ?= 1
ARCHITECT_MAX_RETRIES ?= 2
SPEC_PATH ?=
REPO_ROOT ?= .
REPO_MAP_FORMAT ?= context
COMPUTE_SHIELD_ARGS ?=
RESEARCH_RUNS ?= 3
RESEARCH_OUTPUT ?= docs/results/raw/deepseek-20-repeated.json
RESEARCH_FIXTURE_ROOT ?= tests/fixtures/research_target_repo
RESEARCH_FIXTURE_TASKS ?= data/research_fixture_tasks.json
RESEARCH_FIXTURE_DEEPSEEK_OUTPUT ?= docs/results/raw/fixture-deepseek-repeated.json
RESEARCH_FIXTURE_QWEN_OUTPUT ?= docs/results/raw/fixture-qwen-1.5b-repeated.json
REPORT_INPUT ?= $(RESEARCH_OUTPUT)
REPORT_OUTPUT ?= docs/results/repeated-agent-benchmark-$(shell date +%F).md
REPORT_TITLE ?= Repeated paired coding-agent benchmark
MODEL_COMPARISON_1_5 ?= docs/results/raw/compute-shield-10-qwen-1.5b-2026-08-11.json
MODEL_COMPARISON_3B ?= docs/results/raw/compute-shield-10-qwen-3b-2026-08-11.json
MODEL_COMPARISON_OUTPUT ?= docs/results/local-model-comparison-2026-08-11.md
SESSION_ARGS ?=
RAW_VS_HARNESS_SAMPLES ?= 5
DOC_AGENT ?= deepseek
DOC_MODEL ?=
DOC_OUTPUT ?= $(if $(filter none,$(DOC_AGENT)),,data/library_proposals/$(LIB).docs.md)
SANDBOX_MODE ?= container
CONTAINER_RUNTIME ?= docker
IMAGE ?= agent-small-harness:local
ARTIFACT_ARGS = $(if $(filter 1 true yes,$(SAVE_ARTIFACTS)),--save-artifacts --artifact-root "$(ARTIFACT_ROOT)",)
DOC_ARGS = --doc-agent "$(DOC_AGENT)" $(if $(DOC_MODEL),--doc-model "$(DOC_MODEL)",) $(if $(DOC_OUTPUT),--doc-output "$(DOC_OUTPUT)",)

.PHONY: help bootstrap setup install install-formal install-kernel env-path init-env api-dev tui rust-tui tui_rust start test-rust check research-check research-agent-benchmark research-fixture-deepseek research-fixture-qwen research-report research-model-comparison record-live-session research-compute-shield docker-build sandbox-run agent-benchmark test test-engine-expansion compute-shield test-claude-fixes test-behavior test-engine-edge-cases test-lint-engine test-adversarial test-coding-capability test-coding-capability-architect test-coding-capability-fixture resume-coding-capability test-worker-limit test-worker-limit-auto test-worker-limit-decompose test-worker-limit-architect resume-worker-limit test-python-ladder-parsing test-python-ladder-data test-python-ladder-algorithmic test-python-ladder-stateful test-python-ladder-stateful-architect test-plan-mode-ladder test-raw-vs-harness test-raw-vs-harness-architect test-raw-vs-harness-repeated test-raw-vs-harness-ablation test-formal-experiment structured-spec structured-spec-plan resume-structured-spec repo-map review-run test-treesitter benchmark evaluate-engines aggregate-history discover-library approve-library tool-agent ollama-smoke inference-smoke live-repair day1 clean-history clean-cache clean-generated

help:
	@printf "agent-coder_structure\n\n"
	@printf "Start here:\n"
	@printf "  make setup                       Create .venv, install dependencies, configure .env, build Rust\n"
	@printf "  make start REPO_ROOT=.           Launch the default Rust terminal interface\n"
	@printf "  make check                       Run Python and Rust tests (no model calls)\n"
	@printf "  make research-check              Verify the reproducible research surface (no model calls)\n"
	@printf "  make research-report REPORT_INPUT=...  Render a dated Markdown report from raw JSON\n"
	@printf "  make research-model-comparison         Compare frozen Qwen 1.5B and 3B reports\n"
	@printf "\nDaily work:\n"
	@printf "  make tool-agent TASK='inspect agents'    Run bounded repository tools\n"
	@printf "  make structured-spec-plan SPEC_PATH=...  Create an architect plan only\n"
	@printf "  make structured-spec SPEC_PATH=...       Run the approved structured-spec flow\n"
	@printf "  make repo-map REPO_ROOT=.                Produce repository context\n"
	@printf "  make review-run RUN=<id>                 Review a saved run\n"
	@printf "\nValidation and maintenance:\n"
	@printf "  make test                        Run the full Python test suite\n"
	@printf "  make test-rust                   Run Rust protocol, UI-state, and renderer tests\n"
	@printf "  make docker-build                Build the isolated execution image\n"
	@printf "  make sandbox-run SOURCE=... LANGUAGE=python  Run source without network\n"
	@printf "  make clean-cache                 Remove Python test caches (keeps artifacts and Rust build cache)\n"
	@printf "  make clean-generated             Remove rebuildable Python/Rust caches and run artifacts\n"
	@printf "\nInteractive chat and tools use DeepSeek; local models remain benchmark-only.\n"
	@printf "See setup/README.md, ARCHITECTURE.md, and docs/results/ for details.\n"

bootstrap:
	@test -d .venv || python3 -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -r requirements.txt
	@test -f .env || cp .env.example .env
	$(CARGO) build --release --manifest-path $(RUST_MANIFEST)
	@printf "\nSetup complete. Run: make start REPO_ROOT=.\n"

setup: bootstrap

install:
	$(PYTHON) -m pip install -r requirements.txt

install-formal:
	$(PYTHON) -m pip install -r requirements-formal.txt

install-kernel:
	$(PYTHON) -m pip install -r requirements-kernel.txt

env-path:
	@printf "Env file: %s/.env\n" "$$(pwd)"
	@printf "Add one of these keys there:\n"
	@printf "  ARCHITECT_API_KEY=your_key_here\n"
	@printf "  DEEPSEEK_API_KEY=your_key_here\n"
	@printf "  KERNEL_API_KEY=your_key_here\n"

init-env:
	@test -f .env || cp .env.example .env
	@printf "Env file ready: %s/.env\n" "$$(pwd)"

api-dev:
	$(PYTHON) -m uvicorn api.app:app --reload --host 127.0.0.1 --port 8000

tui:
	$(PYTHON) -m TUI --artifact-root "$(ARTIFACT_ROOT)" --repo-root "$(REPO_ROOT)"

rust-tui:
	PYTHON="$(PYTHON)" $(CARGO) run --manifest-path $(RUST_MANIFEST) -- "$(REPO_ROOT)"

tui_rust: rust-tui

start: rust-tui

test-rust:
	$(CARGO) test --manifest-path $(RUST_MANIFEST)

check: test test-rust

research-check: check

research-agent-benchmark:
	@test -n "$(BASELINE_CMD)" || (echo "Set BASELINE_CMD to a JSON runner command" && exit 1)
	@test -n "$(SHIELDED_CMD)" || (echo "Set SHIELDED_CMD to a JSON runner command" && exit 1)
	$(PYTHON) scripts/run_repeated_agent_benchmark.py --baseline-command "$(BASELINE_CMD)" --shielded-command "$(SHIELDED_CMD)" --runs "$(RESEARCH_RUNS)" --output "$(RESEARCH_OUTPUT)"

research-fixture-deepseek:
	$(PYTHON) scripts/run_repeated_agent_benchmark.py --tasks "$(RESEARCH_FIXTURE_TASKS)" --repository-root "$(RESEARCH_FIXTURE_ROOT)" --baseline-command "$(PYTHON) scripts/run_deepseek_benchmark_agent.py --mode baseline --repository-root $(RESEARCH_FIXTURE_ROOT)" --shielded-command "$(PYTHON) scripts/run_deepseek_benchmark_agent.py --mode shielded --repository-root $(RESEARCH_FIXTURE_ROOT)" --runs "$(RESEARCH_RUNS)" --output "$(RESEARCH_FIXTURE_DEEPSEEK_OUTPUT)"

research-fixture-qwen:
	$(PYTHON) scripts/run_repeated_agent_benchmark.py --tasks "$(RESEARCH_FIXTURE_TASKS)" --repository-root "$(RESEARCH_FIXTURE_ROOT)" --baseline-command "$(PYTHON) scripts/run_ollama_benchmark_agent.py --mode baseline --model qwen2.5-coder:1.5b --repository-root $(RESEARCH_FIXTURE_ROOT)" --shielded-command "$(PYTHON) scripts/run_ollama_benchmark_agent.py --mode shielded --model qwen2.5-coder:1.5b --repository-root $(RESEARCH_FIXTURE_ROOT)" --runs "$(RESEARCH_RUNS)" --output "$(RESEARCH_FIXTURE_QWEN_OUTPUT)"

research-report:
	$(PYTHON) scripts/render_research_report.py --input "$(REPORT_INPUT)" --output "$(REPORT_OUTPUT)" --title "$(REPORT_TITLE)"

research-model-comparison:
	$(PYTHON) scripts/render_local_model_comparison.py --one-point-five "$(MODEL_COMPARISON_1_5)" --three "$(MODEL_COMPARISON_3B)" --output "$(MODEL_COMPARISON_OUTPUT)"

record-live-session:
	@test -n "$(SESSION_ARGS)" || (echo "Set SESSION_ARGS, e.g. --scenario plain_question --prompt-summary '...' --provider deepseek --model deepseek-v4-pro --validation-status not_applicable --outcome answered --output docs/results/raw/session.json" && exit 1)
	$(PYTHON) scripts/record_live_session.py $(SESSION_ARGS)

research-compute-shield:
	$(PYTHON) scripts/run_compute_shield_experiment.py $(COMPUTE_SHIELD_ARGS)

docker-build:
	docker build -t "$(IMAGE)" .

test:
	$(PYTHON) -m unittest discover -s tests

test-engine-expansion:
	$(PYTHON) -m unittest tests.test_engine_expansion tests.test_tui_bridge

compute-shield:
	$(PYTHON) scripts/run_compute_shield.py $(COMPUTE_SHIELD_ARGS)

test-claude-fixes:
	$(PYTHON) -m unittest tests.test_agents_pipeline tests.test_behavior tests.test_benchmarker

test-behavior:
	$(PYTHON) -m unittest tests.test_behavior

test-engine-edge-cases:
	$(PYTHON) -m unittest tests.test_engine_edge_cases

test-lint-engine:
	$(PYTHON) -m unittest tests.test_lint_engine tests.test_benchmarker.BenchmarkerTests.test_lint_engine_blocks_completion_when_pylint_is_missing tests.test_benchmarker.BenchmarkerTests.test_lint_engine_maps_pylint_error_to_policy_violation

test-adversarial:
	$(PYTHON) scripts/run_adversarial_prompts.py --runs "$(RUNS_PATH)"

test-coding-capability:
	$(PYTHON) scripts/run_coding_capability.py --config "$(CONFIG_PATH)" --model "$(MODEL)" --runs "$(RUNS_PATH)" --record-runs $(ARTIFACT_ARGS)

test-coding-capability-architect:
	$(PYTHON) scripts/run_coding_capability.py --config "$(CONFIG_PATH)" --model "$(MODEL)" --runs "$(RUNS_PATH)" --record-runs --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" $(ARTIFACT_ARGS)

test-coding-capability-fixture:
	$(PYTHON) scripts/run_coding_capability.py --config "$(CONFIG_PATH)" --supplier fixture --runs "$(RUNS_PATH)" $(ARTIFACT_ARGS)

resume-coding-capability:
	@test -n "$(RESUME_RUN)" || (echo "Set RESUME_RUN, e.g. make resume-coding-capability RESUME_RUN=matrix_scoring_..." && exit 1)
	$(PYTHON) scripts/run_coding_capability.py --config "$(CONFIG_PATH)" --model "$(MODEL)" --runs "$(RUNS_PATH)" --record-runs --artifact-root "$(ARTIFACT_ROOT)" --resume-run "$(RESUME_RUN)"

test-worker-limit:
	$(PYTHON) scripts/run_worker_limit.py --model "$(MODEL)" --max-retries "$(MAX_RETRIES)" $(ARTIFACT_ARGS)

test-worker-limit-auto:
	$(PYTHON) scripts/run_worker_limit.py --model "auto" --max-retries "$(MAX_RETRIES)" $(ARTIFACT_ARGS)

test-worker-limit-decompose:
	$(PYTHON) scripts/run_worker_limit.py --model "$(MODEL)" --max-retries "$(MAX_RETRIES)" --decompose $(ARTIFACT_ARGS)

test-worker-limit-architect:
	$(PYTHON) scripts/run_worker_limit.py --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" $(ARTIFACT_ARGS)

resume-worker-limit:
	@test -n "$(RESUME_RUN)" || (echo "Set RESUME_RUN, e.g. make resume-worker-limit RESUME_RUN=worker_limit_6_..." && exit 1)
	$(PYTHON) scripts/run_worker_limit.py --model "$(MODEL)" --max-retries "$(MAX_RETRIES)" --artifact-root "$(ARTIFACT_ROOT)" --resume-run "$(RESUME_RUN)"

test-python-ladder-parsing:
	$(PYTHON) scripts/run_worker_limit.py --tasks tests/python_ladders/parsing.json --model "$(MODEL)" --max-retries "$(MAX_RETRIES)" $(ARTIFACT_ARGS)

test-python-ladder-data:
	$(PYTHON) scripts/run_worker_limit.py --tasks tests/python_ladders/data_transform.json --model "$(MODEL)" --max-retries "$(MAX_RETRIES)" $(ARTIFACT_ARGS)

test-python-ladder-algorithmic:
	$(PYTHON) scripts/run_worker_limit.py --tasks tests/python_ladders/algorithmic.json --model "$(MODEL)" --max-retries "$(MAX_RETRIES)" $(ARTIFACT_ARGS)

test-python-ladder-stateful:
	$(PYTHON) scripts/run_worker_limit.py --tasks tests/python_ladders/stateful.json --model "$(MODEL)" --max-retries "$(MAX_RETRIES)" $(ARTIFACT_ARGS)

test-python-ladder-stateful-architect:
	$(PYTHON) scripts/run_worker_limit.py --tasks tests/python_ladders/stateful.json --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" $(ARTIFACT_ARGS)

test-plan-mode-ladder:
	$(PYTHON) scripts/run_plan_mode_ladder.py

test-raw-vs-harness:
	$(PYTHON) scripts/run_raw_vs_harness.py --config "$(CONFIG_PATH)" --model "$(MODEL)"

test-raw-vs-harness-architect:
	$(PYTHON) scripts/run_raw_vs_harness.py --config "$(CONFIG_PATH)" --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)"

test-raw-vs-harness-repeated:
	$(PYTHON) scripts/run_raw_vs_harness.py --config "$(CONFIG_PATH)" --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" --samples "$(RAW_VS_HARNESS_SAMPLES)" --save-artifacts --artifact-root "$(ARTIFACT_ROOT)"

test-raw-vs-harness-ablation:
	$(PYTHON) scripts/run_raw_vs_harness.py --config "$(CONFIG_PATH)" --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" --samples "$(RAW_VS_HARNESS_SAMPLES)" --include-naive-baseline --save-artifacts --artifact-root "$(ARTIFACT_ROOT)"

test-formal-experiment:
	$(PYTHON) scripts/run_formal_experiment.py

structured-spec:
	@test -n "$(SPEC_PATH)" || (echo "Set SPEC_PATH, e.g. make structured-spec SPEC_PATH=examples/specs/my_spec.md" && exit 1)
	$(PYTHON) scripts/run_structured_spec.py --spec "$(SPEC_PATH)" --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" $(ARTIFACT_ARGS)

structured-spec-plan:
	@test -n "$(SPEC_PATH)" || (echo "Set SPEC_PATH, e.g. make structured-spec-plan SPEC_PATH=examples/specs/my_spec.md" && exit 1)
	$(PYTHON) scripts/run_structured_spec.py --spec "$(SPEC_PATH)" --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" --plan-only $(ARTIFACT_ARGS)

resume-structured-spec:
	@test -n "$(SPEC_PATH)" || (echo "Set SPEC_PATH to the original structured spec" && exit 1)
	@test -n "$(RESUME_RUN)" || (echo "Set RESUME_RUN to an artifact run ID" && exit 1)
	$(PYTHON) scripts/run_structured_spec.py --spec "$(SPEC_PATH)" --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" --artifact-root "$(ARTIFACT_ROOT)" --resume-run "$(RESUME_RUN)"

review-run:
	@test -n "$(RUN)" || (echo "Set RUN, e.g. make review-run RUN=worker_limit_6" && exit 1)
	$(PYTHON) scripts/review_run.py "$(RUN)" --artifact-root "$(ARTIFACT_ROOT)"

repo-map:
	$(PYTHON) scripts/run_repo_map.py "$(REPO_ROOT)" --format "$(REPO_MAP_FORMAT)"

test-treesitter:
	$(PYTHON) -m unittest tests.test_treesitter_pipeline

benchmark:
	$(PYTHON) benchmarker.py

evaluate-engines:
	$(PYTHON) -c "from dataclasses import asdict; from engines.evaluator import evaluate_engines; import json; print(json.dumps(asdict(evaluate_engines()), indent=2))"

aggregate-history:
	$(PYTHON) scripts/aggregate_history.py

discover-library:
	@test -n "$(LIB)" || (echo "Set LIB, e.g. make discover-library LIB=json" && exit 1)
	$(PYTHON) scripts/discover_library.py "$(LIB)" $(DOC_ARGS)

approve-library:
	@test -n "$(LIB)" || (echo "Set LIB, e.g. make approve-library LIB=json" && exit 1)
	$(PYTHON) scripts/approve_library.py "$(LIB)"

tool-agent:
	@test -n "$(TASK)" || (echo "Set TASK, e.g. make tool-agent TASK='inspect rust_tui/src/main.rs'" && exit 1)
	$(PYTHON) scripts/run_tool_agent.py "$(TASK)" --repo-root "$(REPO_ROOT)"

sandbox-run:
	@test -n "$(SOURCE)" || (echo "Set SOURCE to a source file" && exit 1)
	@test -n "$(LANGUAGE)" || (echo "Set LANGUAGE=python|c|cpp|rust|javascript" && exit 1)
	$(PYTHON) scripts/run_sandbox.py "$(SOURCE)" --language "$(LANGUAGE)" --mode "$(SANDBOX_MODE)" --runtime "$(CONTAINER_RUNTIME)"

agent-benchmark:
	@test -n "$(BASELINE_CMD)" || (echo "Set BASELINE_CMD to a JSON runner command" && exit 1)
	@test -n "$(SHIELDED_CMD)" || (echo "Set SHIELDED_CMD to a JSON runner command" && exit 1)
	$(PYTHON) scripts/run_agent_benchmark.py --baseline-command "$(BASELINE_CMD)" --shielded-command "$(SHIELDED_CMD)" $(if $(BENCHMARK_OUTPUT),--output "$(BENCHMARK_OUTPUT)",)

ollama-smoke:
	$(PYTHON) -c "from benchmarker import build_ollama_controller; controller = build_ollama_controller(debug=True); print(controller.name)"

inference-smoke:
	$(PYTHON) scripts/test_inference.py

live-repair:
	$(PYTHON) scripts/run_live_repair.py

day1: benchmark test

clean-history:
	$(PYTHON) -c "import json, pathlib; p = pathlib.Path('history.json'); data = json.loads(p.read_text()); data['generations'] = []; p.write_text(json.dumps(data, indent=2) + '\n')"

clean-cache:
	@find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	@rm -rf .pytest_cache
	@printf "Removed Python caches. Artifacts, .env, local history, and Rust build output were kept.\n"

clean-generated: clean-cache
	@rm -rf artifacts rust_tui/target
	@printf "Removed rebuildable artifacts and Rust build output. Local history, .env, and source files were kept.\n"
