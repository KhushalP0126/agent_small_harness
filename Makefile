PYTHON ?= python3
RUNS_PATH ?= data/runs.jsonl
CONFIG_PATH ?= config.yaml
ARTIFACT_ROOT ?= artifacts/runs
SAVE_ARTIFACTS ?= 0
MODEL ?= qwen2.5-coder:1.5b
RUN ?=
ARCHITECT_AFTER ?= 1
ARCHITECT_MAX_RETRIES ?= 2
ARTIFACT_ARGS = $(if $(filter 1 true yes,$(SAVE_ARTIFACTS)),--save-artifacts --artifact-root "$(ARTIFACT_ROOT)",)

.PHONY: help install install-formal test test-behavior test-engine-edge-cases test-lint-engine test-adversarial test-coding-capability test-coding-capability-architect test-coding-capability-fixture test-worker-limit test-worker-limit-auto test-worker-limit-decompose test-python-ladder-parsing test-python-ladder-data test-python-ladder-algorithmic test-plan-mode-ladder test-raw-vs-harness test-formal-experiment review-run test-treesitter benchmark evaluate-engines aggregate-history discover-library approve-library ollama-smoke inference-smoke live-repair day1 clean-history

help:
	@printf "Targets:\n"
	@printf "  make install       Install optional tree-sitter deps for C/C++ support\n"
	@printf "  make install-formal Install optional Deal/CrossHair formal-verification deps\n"
	@printf "  make test          Run unit tests\n"
	@printf "  make test-behavior Run behavior validation tests\n"
	@printf "  make test-engine-edge-cases Run focused engine boundary/false-positive tests\n"
	@printf "  make test-lint-engine Run focused lint-engine tests\n"
	@printf "  make test-adversarial Run trap prompts through the PEV loop\n"
	@printf "  make test-coding-capability Test small-worker code generation through engines and behavior gates\n"
	@printf "  make test-coding-capability-architect Test small-worker generation with API architect escalation\n"
	@printf "    Add SAVE_ARTIFACTS=1 to save attempt files under ARTIFACT_ROOT\n"
	@printf "  make test-coding-capability-fixture Verify the coding-capability harness without Ollama\n"
	@printf "  make test-worker-limit Push the local worker through a harder-and-harder task ladder\n"
	@printf "  make test-worker-limit-auto Run worker ladder with config-driven model routing\n"
	@printf "  make test-worker-limit-decompose Run worker-limit ladder with skeleton decomposition prompts\n"
	@printf "  make test-python-ladder-parsing Run the parsing-focused Python ladder\n"
	@printf "  make test-python-ladder-data Run the data-transform Python ladder\n"
	@printf "  make test-python-ladder-algorithmic Run the algorithmic Python ladder\n"
	@printf "  make test-plan-mode-ladder Test Plan Mode extraction on progressively harder prompts\n"
	@printf "  make test-raw-vs-harness Compare raw one-shot generation with full harness validation\n"
	@printf "  make test-formal-experiment Run optional CrossHair semantic-validation smoke experiment\n"
	@printf "  make review-run RUN=<id-or-path> Render a human-review summary for an artifact run\n"
	@printf "  make test-treesitter Run the C/C++ tree-sitter pipeline tests\n"
	@printf "  make benchmark     Run the Day 1 benchmark pipeline\n"
	@printf "  make evaluate-engines Score the engine suite on data snippets\n"
	@printf "  make aggregate-history Build routing stats from data/runs.jsonl\n"
	@printf "  make discover-library LIB=name Write data/library_proposals/name.json\n"
	@printf "  make approve-library LIB=name Merge approved proposal into library registry\n"
	@printf "  make ollama-smoke  Verify the Ollama-backed controller can be constructed\n"
	@printf "  make inference-smoke Verify the configured Ollama model responds\n"
	@printf "  make live-repair   Run Ollama repair loop on mixed_hard_case.py\n"
	@printf "  make day1          Run benchmark and tests\n"
	@printf "  make clean-history Reset generated history entries\n"

install:
	$(PYTHON) -m pip install -r requirements.txt

install-formal:
	$(PYTHON) -m pip install -r requirements-formal.txt

test:
	$(PYTHON) -m unittest discover -s tests

test-behavior:
	$(PYTHON) -m unittest tests.test_behavior

test-engine-edge-cases:
	$(PYTHON) -m unittest tests.test_engine_edge_cases

test-lint-engine:
	$(PYTHON) -m unittest tests.test_benchmarker.BenchmarkerTests.test_lint_engine_is_optional_when_pylint_missing tests.test_benchmarker.BenchmarkerTests.test_lint_engine_maps_pylint_error_to_policy_violation

test-adversarial:
	$(PYTHON) scripts/run_adversarial_prompts.py --runs "$(RUNS_PATH)"

test-coding-capability:
	$(PYTHON) scripts/run_coding_capability.py --config "$(CONFIG_PATH)" --model "$(MODEL)" --runs "$(RUNS_PATH)" --record-runs $(ARTIFACT_ARGS)

test-coding-capability-architect:
	$(PYTHON) scripts/run_coding_capability.py --config "$(CONFIG_PATH)" --model "$(MODEL)" --runs "$(RUNS_PATH)" --record-runs --max-retries "$(ARCHITECT_MAX_RETRIES)" --architect-after-repair-attempts "$(ARCHITECT_AFTER)" $(ARTIFACT_ARGS)

test-coding-capability-fixture:
	$(PYTHON) scripts/run_coding_capability.py --config "$(CONFIG_PATH)" --supplier fixture --runs "$(RUNS_PATH)" $(ARTIFACT_ARGS)

test-worker-limit:
	$(PYTHON) scripts/run_worker_limit.py --model "$(MODEL)" --max-retries "1" $(ARTIFACT_ARGS)

test-worker-limit-auto:
	$(PYTHON) scripts/run_worker_limit.py --model "auto" --max-retries "1" $(ARTIFACT_ARGS)

test-worker-limit-decompose:
	$(PYTHON) scripts/run_worker_limit.py --model "$(MODEL)" --max-retries "1" --decompose $(ARTIFACT_ARGS)

test-python-ladder-parsing:
	$(PYTHON) scripts/run_worker_limit.py --tasks tests/python_ladders/parsing.json --model "$(MODEL)" --max-retries "1" $(ARTIFACT_ARGS)

test-python-ladder-data:
	$(PYTHON) scripts/run_worker_limit.py --tasks tests/python_ladders/data_transform.json --model "$(MODEL)" --max-retries "1" $(ARTIFACT_ARGS)

test-python-ladder-algorithmic:
	$(PYTHON) scripts/run_worker_limit.py --tasks tests/python_ladders/algorithmic.json --model "$(MODEL)" --max-retries "1" $(ARTIFACT_ARGS)

test-plan-mode-ladder:
	$(PYTHON) scripts/run_plan_mode_ladder.py

test-raw-vs-harness:
	$(PYTHON) scripts/run_raw_vs_harness.py --config "$(CONFIG_PATH)" --model "$(MODEL)"

test-formal-experiment:
	$(PYTHON) scripts/run_formal_experiment.py

review-run:
	@test -n "$(RUN)" || (echo "Set RUN, e.g. make review-run RUN=worker_limit_6" && exit 1)
	$(PYTHON) scripts/review_run.py "$(RUN)" --artifact-root "$(ARTIFACT_ROOT)"

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
	$(PYTHON) scripts/discover_library.py "$(LIB)"

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
