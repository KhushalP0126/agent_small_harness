PYTHON ?= python3

.PHONY: help install test test-behavior test-treesitter benchmark evaluate-engines ollama-smoke inference-smoke live-repair day1 clean-history

help:
	@printf "Targets:\n"
	@printf "  make install       Install optional tree-sitter deps for C/C++ support\n"
	@printf "  make test          Run unit tests\n"
	@printf "  make test-behavior Run behavior validation tests\n"
	@printf "  make test-treesitter Run the C/C++ tree-sitter pipeline tests\n"
	@printf "  make benchmark     Run the Day 1 benchmark pipeline\n"
	@printf "  make evaluate-engines Score the engine suite on data snippets\n"
	@printf "  make ollama-smoke  Verify the Ollama-backed controller can be constructed\n"
	@printf "  make inference-smoke Verify the configured Ollama model responds\n"
	@printf "  make live-repair   Run Ollama repair loop on mixed_hard_case.py\n"
	@printf "  make day1          Run benchmark and tests\n"
	@printf "  make clean-history Reset generated history entries\n"

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m unittest discover -s tests

test-behavior:
	$(PYTHON) -m unittest tests.test_behavior

test-treesitter:
	$(PYTHON) -m unittest tests.test_treesitter_pipeline

benchmark:
	$(PYTHON) benchmarker.py

evaluate-engines:
	$(PYTHON) -c "from dataclasses import asdict; from engines.evaluator import evaluate_engines; import json; print(json.dumps(asdict(evaluate_engines()), indent=2))"

ollama-smoke:
	$(PYTHON) -c "from benchmarker import build_ollama_controller; controller = build_ollama_controller(debug=True); print(controller.name)"

inference-smoke:
	$(PYTHON) scripts/test_inference.py

live-repair:
	$(PYTHON) scripts/run_live_repair.py

day1: benchmark test

clean-history:
	$(PYTHON) -c "import json, pathlib; p = pathlib.Path('history.json'); data = json.loads(p.read_text()); data['generations'] = []; p.write_text(json.dumps(data, indent=2) + '\n')"
