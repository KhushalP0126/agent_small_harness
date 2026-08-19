# Multi-function formal repair corpus (no-guidance vs generic vs routed)

> Generated: 2026-08-19 · Source schema: 4

## Reproducibility

- Commit: `05d82cd10a8f1c0f2491d4650efdc61bf0cc1eec`
- Corpus: `data/formal_repair_multifunction_benchmark_tasks.json`
- Corpus SHA-256: `03d37c09d74dd347b33c8a6a95340be8c86565627b8d5a887b9538fa05f7c2f9`
- Repetitions: `5`

## Configured variants

- baseline: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- generic: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- routed: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- Scope: `python-crosshair-only`

## Provider health gate

- **Eligible:** every recorded provider response was usable; aggregate comparison is allowed.

## Three-arm comparison

| Measure | No formal guidance | Generic directive | Failure-mode routed |
| --- | ---: | ---: | ---: |
| Successful tasks | 1.80 ± 0.45 | 1.00 ± 0.00 | 1.00 ± 0.00 |
| Model tokens | 590.20 ± 1.79 | 745.80 ± 1.10 | 609.00 ± 0.00 |
| Tool calls | 2.00 ± 0.00 | 2.00 ± 0.00 | 2.00 ± 0.00 |
| Wall-clock seconds | 9.28 ± 1.45 | 11.27 ± 0.17 | 9.72 ± 0.42 |

## Regression and coverage

| Measure | Generic directive | Failure-mode routed |
| --- | ---: | ---: |
| Regression rate | 60.00% | 60.00% |
| Baseline-pass regressions | 1.00 | 1.00 |
| Routed known-signature coverage | n/a | 0.00% |

Regression means a task that passed without repair but failed after the named repair strategy. Routed coverage is the fraction of task failures matched to a pre-diagnosed signature; unclassified cases receive no generic verifier directive.

## Failures retained

- Run 1: `formal-quadruple-value` (generic) — CrossHair found a contract or assertion issue: quadruple_value(1) (which returns 2)
- Run 1: `formal-quadruple-value` (routed) — CrossHair found a contract or assertion issue: quadruple_value(1) (which returns 2)
- Run 2: `formal-quadruple-value` (generic) — CrossHair found a contract or assertion issue: quadruple_value(1) (which returns 2)
- Run 2: `formal-quadruple-value` (routed) — CrossHair found a contract or assertion issue: quadruple_value(1) (which returns 2)
- Run 2: `formal-maximum-of-three` (baseline) — CrossHair found a contract or assertion issue: maximum_of_three(0, 0, 1) (which returns 0)
- Run 3: `formal-quadruple-value` (generic) — CrossHair found a contract or assertion issue: quadruple_value(1) (which returns 2)
- Run 3: `formal-quadruple-value` (routed) — CrossHair found a contract or assertion issue: quadruple_value(1) (which returns 2)
- Run 4: `formal-quadruple-value` (generic) — CrossHair found a contract or assertion issue: quadruple_value(1) (which returns 2)
- Run 4: `formal-quadruple-value` (routed) — CrossHair found a contract or assertion issue: quadruple_value(1) (which returns 2)
- Run 5: `formal-quadruple-value` (generic) — CrossHair found a contract or assertion issue: quadruple_value(1) (which returns 2)
- Run 5: `formal-quadruple-value` (routed) — CrossHair found a contract or assertion issue: quadruple_value(1) (which returns 2)

## Raw evidence

This report is a rendering of a versioned JSON input. It retains per-task outcomes, exact prompts, candidates, routing decisions, and verifier evidence; it does not replace the raw artifact.
