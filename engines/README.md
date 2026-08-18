# Engines

Deterministic checks belong here: parsing, static analysis, import risk,
compilation, and cost/complexity signals. Agents propose work; engines provide
evidence. Python, C, C++, Rust, and JavaScript share the structural pipeline
through the project IR where their toolchains are available.

- `compilation_engine.py` provides compilation/syntax gates for C/C++, Rust,
  and JavaScript.
- `cost_engine.py` reports algorithmic-cost signals.
- `lint_engine.py` and `pylint_engine.py` provide lint checks.
- `tree_sitter_engine.py`, `import_extractors.py`, and `import_risk.py` supply
  language-aware parsing and dependency evidence.

Higher-level behavior, policy, and formal checks remain in
[`../validation/`](../validation/) during the compatibility migration.
