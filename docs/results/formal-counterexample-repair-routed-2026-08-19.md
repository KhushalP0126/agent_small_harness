# Failure-mode-routed formal repair (order-pair timeout fix verified)

> Generated: 2026-08-19 · Source schema: 4

## Reproducibility

- Commit: `05d82cd10a8f1c0f2491d4650efdc61bf0cc1eec`
- Corpus: `data/formal_repair_diverse_benchmark_tasks.json`
- Corpus SHA-256: `2dc1305ba978fd0af2f5162273e33d34e5fc444e1a08e3504e549a65da04d689`
- Repetitions: `3`

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
| Successful tasks | 9.67 ± 0.58 | 7.33 ± 0.58 | 11.00 ± 0.00 |
| Model tokens | 2487.00 ± 5.20 | 3284.67 ± 12.58 | 2718.67 ± 8.08 |
| Tool calls | 11.00 ± 0.00 | 11.00 ± 0.00 | 11.00 ± 0.00 |
| Wall-clock seconds | 60.07 ± 5.49 | 71.15 ± 6.19 | 61.27 ± 7.69 |

## Regression and coverage

| Measure | Generic directive | Failure-mode routed |
| --- | ---: | ---: |
| Regression rate | 24.07% | 0.00% |
| Baseline-pass regressions | 2.33 | 0.00 |
| Routed known-signature coverage | n/a | 27.27% |

Regression means a task that passed without repair but failed after the named repair strategy. Routed coverage is the fraction of task failures matched to a pre-diagnosed signature; unclassified cases receive no generic verifier directive.

## Failures retained

- Run 1: `formal-nonnegative` (baseline) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 1: `formal-nonnegative` (generic) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 1: `formal-is-even` (generic) — CrossHair found a contract or assertion issue: is_even(1)
- Run 1: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 1: `formal-order-pair` (generic) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 1: `formal-prefix-sum` (generic) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)
- Run 2: `formal-nonnegative` (generic) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 2: `formal-is-even` (generic) — CrossHair found a contract or assertion issue: is_even(1)
- Run 2: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 2: `formal-order-pair` (generic) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 2: `formal-prefix-sum` (generic) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)
- Run 3: `formal-nonnegative` (generic) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 3: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 3: `formal-order-pair` (generic) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 3: `formal-prefix-sum` (generic) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)

## Raw evidence

This report is a rendering of a versioned JSON input. It retains per-task outcomes, exact prompts, candidates, routing decisions, and verifier evidence; it does not replace the raw artifact.
