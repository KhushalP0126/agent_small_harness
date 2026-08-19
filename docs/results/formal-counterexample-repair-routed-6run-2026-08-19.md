# Failure-mode-routed formal repair (6-repetition confirmation)

> Generated: 2026-08-19 · Source schema: 4

## Reproducibility

- Commit: `05d82cd10a8f1c0f2491d4650efdc61bf0cc1eec`
- Corpus: `data/formal_repair_diverse_benchmark_tasks.json`
- Corpus SHA-256: `2dc1305ba978fd0af2f5162273e33d34e5fc444e1a08e3504e549a65da04d689`
- Repetitions: `6`

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
| Successful tasks | 9.00 ± 0.89 | 6.83 ± 1.17 | 10.50 ± 0.55 |
| Model tokens | 2493.67 ± 4.93 | 3276.50 ± 15.81 | 2719.67 ± 9.58 |
| Tool calls | 11.00 ± 0.00 | 11.00 ± 0.00 | 11.00 ± 0.00 |
| Wall-clock seconds | 45.17 ± 8.75 | 52.88 ± 7.98 | 49.99 ± 7.89 |

## Regression and coverage

| Measure | Generic directive | Failure-mode routed |
| --- | ---: | ---: |
| Regression rate | 27.22% | 5.60% |
| Baseline-pass regressions | 2.50 | 0.50 |
| Routed known-signature coverage | n/a | 27.27% |

Regression means a task that passed without repair but failed after the named repair strategy. Routed coverage is the fraction of task failures matched to a pre-diagnosed signature; unclassified cases receive no generic verifier directive.

## Failures retained

- Run 1: `formal-clamp-value` (routed) — CrossHair timed out: Command '['/usr/local/bin/python3', '-m', 'crosshair', 'check', '/var/folders/fr/zgwhhqkx1k953vglp24mbbkh0000gn/T/agent_harness_crosshair_4jnzpn9e/candidate.py', '--per_condition_timeout', '3.0']' timed out after 4.0 seconds
- Run 1: `formal-nonnegative` (generic) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 1: `formal-is-even` (generic) — CrossHair found a contract or assertion issue: is_even(1)
- Run 1: `formal-order-pair` (generic) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 1: `formal-trim-text` (baseline) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')
- Run 1: `formal-prefix-sum` (generic) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)
- Run 2: `formal-nonnegative` (generic) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 2: `formal-is-even` (generic) — CrossHair found a contract or assertion issue: is_even(1)
- Run 2: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 2: `formal-order-pair` (generic) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 2: `formal-trim-text` (generic) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')
- Run 2: `formal-prefix-sum` (generic) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)
- Run 3: `formal-nonnegative` (baseline) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 3: `formal-nonnegative` (generic) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 3: `formal-is-even` (generic) — CrossHair found a contract or assertion issue: is_even(1)
- Run 3: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 3: `formal-order-pair` (generic) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 3: `formal-prefix-sum` (generic) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)
- Run 4: `formal-nonnegative` (baseline) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 4: `formal-nonnegative` (generic) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 4: `formal-double` (generic) — CrossHair found a contract or assertion issue: double(-1)
- Run 4: `formal-is-even` (generic) — CrossHair found a contract or assertion issue: is_even(1)
- Run 4: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 4: `formal-order-pair` (generic) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 4: `formal-trim-text` (baseline) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')
- Run 4: `formal-trim-text` (generic) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')
- Run 4: `formal-prefix-sum` (generic) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)
- Run 5: `formal-nonnegative` (baseline) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 5: `formal-nonnegative` (generic) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 5: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 5: `formal-order-pair` (generic) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 5: `formal-trim-text` (baseline) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')
- Run 5: `formal-prefix-sum` (generic) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)
- Run 5: `formal-prefix-sum` (routed) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)
- Run 6: `formal-clamp-value` (routed) — CrossHair timed out: Command '['/usr/local/bin/python3', '-m', 'crosshair', 'check', '/var/folders/fr/zgwhhqkx1k953vglp24mbbkh0000gn/T/agent_harness_crosshair_ymjy94ar/candidate.py', '--per_condition_timeout', '3.0']' timed out after 4.0 seconds
- Run 6: `formal-nonnegative` (baseline) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 6: `formal-nonnegative` (generic) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 6: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 6: `formal-order-pair` (generic) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 6: `formal-prefix-sum` (generic) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)

## Raw evidence

This report is a rendering of a versioned JSON input. It retains per-task outcomes, exact prompts, candidates, routing decisions, and verifier evidence; it does not replace the raw artifact.
