PYTHON ?= python3
RUNS_PATH ?= data/runs.jsonl
CONFIG_PATH ?= config.yaml
ARTIFACT_ROOT ?= artifacts/runs
SAVE_ARTIFACTS ?= 1
MODEL ?= qwen2.5-coder:1.5b
MAX_RETRIES ?= 3
RUN ?=
ARCHITECT_AFTER ?= 1
ARCHITECT_MAX_RETRIES ?= 2
SPEC_PATH ?=
REPO_ROOT ?= .
REPO_MAP_FORMAT ?= context
DOC_AGENT ?= deepseek
DOC_MODEL ?=
DOC_OUTPUT ?= $(if $(filter none,$(DOC_AGENT)),,data/library_proposals/$(LIB).docs.md)
IMAGE ?= agent-small-harness:local
ARTIFACT_ARGS = $(if $(filter 1 true yes,$(SAVE_ARTIFACTS)),--save-artifacts --artifact-root "$(ARTIFACT_ROOT)",)
DOC_ARGS = --doc-agent "$(DOC_AGENT)" $(if $(DOC_MODEL),--doc-model "$(DOC_MODEL)",) $(if $(DOC_OUTPUT),--doc-output "$(DOC_OUTPUT)",)

.PHONY: help install install-formal install-kernel env-path init-env api-dev docker-build test test-claude-fixes test-behavior test-engine-edge-cases test-lint-engine test-adversarial test-coding-capability test-coding-capability-architect test-coding-capability-fixture test-worker-limit test-worker-limit-auto test-worker-limit-decompose test-worker-limit-architect test-python-ladder-parsing test-python-ladder-data test-python-ladder-algorithmic test-python-ladder-stateful test-python-ladder-stateful-architect test-plan-mode-ladder test-raw-vs-harness test-formal-experiment structured-spec structured-spec-plan repo-map review-run test-treesitter benchmark evaluate-engines aggregate-history discover-library approve-library ollama-smoke inference-smoke live-repair day1 clean-history

help:
	@printf "Agent Small Harness commands\n"
	@printf "\nSetup:\n"
	@printf "  make install                         Install optional tree-sitter deps for C/C++ support\n"
	@printf "  make install-formal                  Install optional Deal/CrossHair formal-verification deps\n"
	@printf "  make install-kernel                  Install optional Kernel browser documentation deps\n"
	@printf "  make env-path                        Print the local .env path and supported API key names\n"
	@printf "  make init-env                        Create .env from .env.example if it does not already exist\n"
	@printf "  make api-dev                         Run the synchronous FastAPI service locally\n"
	@printf "  make docker-build                    Build the local API container image\n"
	@printf "\nDeterministic validation, no model calls:\n"
	@printf "  make test                            Run the full unit test suite\n"
	@printf "  make test-claude-fixes               Run focused controller, historian, behavior, and telemetry tests\n"
	@printf "  make test-behavior                   Run behavior validator tests\n"
	@printf "  make test-engine-edge-cases          Run engine boundary and false-positive tests\n"
	@printf "  make test-lint-engine                Run focused Pylint-engine tests\n"
	@printf "  make test-treesitter                 Run optional C/C++ tree-sitter pipeline tests\n"
	@printf "  make evaluate-engines                Score static engines against data/engine_cases.json\n"
	@printf "  make repo-map                        Map a repo's functions/vars/loops/imports (context|json|mermaid)\n"
	@printf "  make benchmark                       Run the Day 1 benchmark pipeline\n"
	@printf "  make test-coding-capability-fixture  Verify coding-capability plumbing without Ollama\n"
	@printf "\nLive model runs:\n"
	@printf "  make inference-smoke                 Verify the configured Ollama model responds\n"
	@printf "  make ollama-smoke                    Verify the Ollama-backed controller can be constructed\n"
	@printf "  make live-repair                     Run Ollama repair loop on data/snippets/mixed_hard_case.py\n"
	@printf "  make test-coding-capability          Run model codegen through engines and behavior gates\n"
	@printf "  make test-coding-capability-architect Run model codegen with API architect escalation\n"
	@printf "  make test-raw-vs-harness             Compare raw one-shot generation with full harness validation\n"
	@printf "  make structured-spec SPEC_PATH=path   Run any external structured spec through Plan Mode, worker, architect, and gates\n"
	@printf "  make structured-spec-plan SPEC_PATH=path Ask architect for queue plan, print JSON, then stop before worker generation\n"
	@printf "\nWorker ladders:\n"
	@printf "  make test-worker-limit               Push MODEL through harder worker-limit tasks\n"
	@printf "  make test-worker-limit-auto          Use config.yaml difficulty model routing\n"
	@printf "  make test-worker-limit-decompose     Add skeleton decomposition prompts to worker-limit tasks\n"
	@printf "  make test-worker-limit-architect     Use API architect escalation on worker-limit tasks\n"
	@printf "  make test-python-ladder-parsing      Run parsing-focused Python ladder\n"
	@printf "  make test-python-ladder-data         Run data-transform Python ladder\n"
	@printf "  make test-python-ladder-algorithmic  Run algorithmic Python ladder\n"
	@printf "  make test-python-ladder-stateful     Run stateful parser/event Python ladder\n"
	@printf "  make test-python-ladder-stateful-architect Run stateful ladder with API architect escalation\n"
	@printf "  make test-plan-mode-ladder           Test Plan Mode extraction on progressively harder prompts\n"
	@printf "\nArtifacts, history, and review:\n"
	@printf "  make review-run RUN=<id-or-path>     Render a human-review summary for an artifact run\n"
	@printf "  make aggregate-history               Build routing stats from data/runs.jsonl\n"
	@printf "  make clean-history                   Reset generated history entries\n"
	@printf "\nLibrary registry:\n"
	@printf "  make discover-library LIB=name       Ask DeepSeek for docs and write proposal plus Markdown guide\n"
	@printf "  make discover-library LIB=name DOC_AGENT=qwen Ask local Qwen for documentation candidates\n"
	@printf "  make discover-library LIB=name DOC_AGENT=kernel Verify documentation with a Kernel browser\n"
	@printf "  make discover-library LIB=name DOC_AGENT=none Write proposal without model documentation search\n"
	@printf "  make approve-library LIB=name        Merge approved proposal into library registry\n"
	@printf "\nConvenience:\n"
	@printf "  make test-adversarial                Run trap prompts through the PEV loop\n"
	@printf "  make test-formal-experiment          Run optional CrossHair semantic-validation smoke experiment\n"
	@printf "  make day1                            Run benchmark and tests\n"
	@printf "\nCommon variables:\n"
	@printf "  MODEL=qwen2.5-coder:3b               Local Ollama worker model; default is $(MODEL)\n"
	@printf "  MAX_RETRIES=3                        Small-worker retry budget for ladder targets; default is $(MAX_RETRIES)\n"
	@printf "  ARCHITECT_AFTER=1                    Escalate to architect after this many failed repairs; default is $(ARCHITECT_AFTER)\n"
	@printf "  ARCHITECT_MAX_RETRIES=2              Total repair budget for architect targets; default is $(ARCHITECT_MAX_RETRIES)\n"
	@printf "  SAVE_ARTIFACTS=1                     Save attempts, prompts, diffs, and validations; default is $(SAVE_ARTIFACTS)\n"
	@printf "  ARTIFACT_ROOT=artifacts/runs         Artifact directory; default is $(ARTIFACT_ROOT)\n"
	@printf "  SPEC_PATH=path/to/spec.md            Structured-spec input path\n"
	@printf "  REPO_ROOT=.                          Repo root for make repo-map; default is $(REPO_ROOT)\n"
	@printf "  REPO_MAP_FORMAT=context|json|mermaid Output for make repo-map; default is $(REPO_MAP_FORMAT)\n"
	@printf "  DOC_AGENT=deepseek|qwen|kernel|none  Backend for library documentation search; default is $(DOC_AGENT)\n"
	@printf "  DOC_MODEL=name                       Optional doc-search model override\n"
	@printf "  DOC_OUTPUT=path                      Markdown docs output path; default is data/library_proposals/<LIB>.docs.md for model search\n"
	@printf "  IMAGE=agent-small-harness:local      Docker image tag for docker-build\n"
	@printf "\nExamples:\n"
	@printf "  make test-worker-limit MODEL=qwen2.5-coder:3b SAVE_ARTIFACTS=1\n"
	@printf "  make test-worker-limit-auto SAVE_ARTIFACTS=1\n"
	@printf "  make test-worker-limit-architect MODEL=qwen2.5-coder:3b SAVE_ARTIFACTS=1\n"
	@printf "  make review-run RUN=worker_limit_6\n"

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

docker-build:
	docker build -t "$(IMAGE)" .

test:
	$(PYTHON) -m unittest discover -s tests

test-claude-fixes:
	$(PYTHON) -m unittest tests.test_agents_pipeline tests.test_behavior tests.test_benchmarker

test-behavior:
	$(PYTHON) -m unittest tests.test_behavior

test-engine-edge-cases:
	$(PYTHON) -m unittest tests.test_engine_edge_cases

test-lint-engine:
	$(PYTHON) -m unittest tests.test_benchmarker.BenchmarkerTests.test_lint_engine_blocks_completion_when_pylint_is_missing tests.test_benchmarker.BenchmarkerTests.test_lint_engine_maps_pylint_error_to_policy_violation

test-adversarial:
	$(PYTHON) scripts/run_adversarial_prompts.py --runs "$(RUNS_PATH)"

test-coding-capability:
	$(PYTHON) scripts/run_coding_capability.py --config "$(CONFIG_PATH)" --model "$(MODEL)" --runs "$(RUNS_PATH)" --record-runs $(ARTIFACT_ARGS)

test-coding-capability-architect:
	$(PYTHON) scripts/run_coding_capability.py --config "$(CONFIG_PATH)" --model "$(MODEL)" --runs "$(RUNS_PATH)" --record-runs --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" $(ARTIFACT_ARGS)

test-coding-capability-fixture:
	$(PYTHON) scripts/run_coding_capability.py --config "$(CONFIG_PATH)" --supplier fixture --runs "$(RUNS_PATH)" $(ARTIFACT_ARGS)

test-worker-limit:
	$(PYTHON) scripts/run_worker_limit.py --model "$(MODEL)" --max-retries "$(MAX_RETRIES)" $(ARTIFACT_ARGS)

test-worker-limit-auto:
	$(PYTHON) scripts/run_worker_limit.py --model "auto" --max-retries "$(MAX_RETRIES)" $(ARTIFACT_ARGS)

test-worker-limit-decompose:
	$(PYTHON) scripts/run_worker_limit.py --model "$(MODEL)" --max-retries "$(MAX_RETRIES)" --decompose $(ARTIFACT_ARGS)

test-worker-limit-architect:
	$(PYTHON) scripts/run_worker_limit.py --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" $(ARTIFACT_ARGS)

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

test-formal-experiment:
	$(PYTHON) scripts/run_formal_experiment.py

structured-spec:
	@test -n "$(SPEC_PATH)" || (echo "Set SPEC_PATH, e.g. make structured-spec SPEC_PATH=examples/specs/my_spec.md" && exit 1)
	$(PYTHON) scripts/run_structured_spec.py --spec "$(SPEC_PATH)" --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" $(ARTIFACT_ARGS)

structured-spec-plan:
	@test -n "$(SPEC_PATH)" || (echo "Set SPEC_PATH, e.g. make structured-spec-plan SPEC_PATH=examples/specs/my_spec.md" && exit 1)
	$(PYTHON) scripts/run_structured_spec.py --spec "$(SPEC_PATH)" --model "$(MODEL)" --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" --plan-only $(ARTIFACT_ARGS)

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

ollama-smoke:
	$(PYTHON) -c "from benchmarker import build_ollama_controller; controller = build_ollama_controller(debug=True); print(controller.name)"

inference-smoke:
	$(PYTHON) scripts/test_inference.py

live-repair:
	$(PYTHON) scripts/run_live_repair.py

day1: benchmark test

clean-history:
	$(PYTHON) -c "import json, pathlib; p = pathlib.Path('history.json'); data = json.loads(p.read_text()); data['generations'] = []; p.write_text(json.dumps(data, indent=2) + '\n')"
