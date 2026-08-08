# Engines

Deterministic checks belong here: syntax, static analysis, compilation, and
cost/complexity signals. Agents propose work; engines provide evidence.

- `compilation_engine.py` is the C/C++ compiler gate.
- `cost_engine.py` reports algorithmic-cost signals.
- `lint_engine.py` and `pylint_engine.py` provide lint checks.
- `tree_sitter_engine.py` supplies optional language-aware parsing.

Higher-level behavior, policy, and formal checks remain in
[`../validation/`](../validation/) during the compatibility migration.
