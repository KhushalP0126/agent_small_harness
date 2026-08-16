# Formal counterexample-guided repair: general-directive control

> Generated: 2026-08-16 · Source schema: 2

## Reproducibility

- Commit: `1acc328c55061cd123478f03a858f22ff8383ab1`
- Working tree dirty: `True`
- OS: `macOS-26.5.1-arm64-arm-64bit`
- Python: `3.11.9`
- Corpus: `data/formal_repair_diverse_benchmark_tasks.json`
- Corpus SHA-256: `2dc1305ba978fd0af2f5162273e33d34e5fc444e1a08e3504e549a65da04d689`
- Repetitions: `3`

## Configured variants

- baseline: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- shielded: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- Scope: `python-crosshair-only`

## Descriptive summary

| Measure | Baseline mean ± SD | Shielded mean ± SD | Difference |
| --- | ---: | ---: | ---: |
| Successful tasks | 9.00 ± 0.00 | 6.67 ± 0.58 | -2.33 |
| Model tokens | 2828.33 ± 0.58 | 3283.67 ± 16.01 | +455.33 |
| Tool calls | 11.00 ± 0.00 | 11.00 ± 0.00 | +0.00 |
| Wall-clock seconds | 53.27 ± 13.20 | 60.66 ± 9.78 | +7.39 |

## Interpretation

The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings.

## Failures retained

- Run 1: `formal-nonnegative` (shielded) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 1: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 1: `formal-order-pair` (shielded) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 1: `formal-trim-text` (baseline) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')
- Run 1: `formal-trim-text` (shielded) — CrossHair timed out: Command '['/usr/local/bin/python3', '-m', 'crosshair', 'check', '/var/folders/fr/zgwhhqkx1k953vglp24mbbkh0000gn/T/agent_harness_crosshair_vhml02my/candidate.py', '--per_condition_timeout', '3.0']' timed out after 4.0 seconds
- Run 1: `formal-prefix-sum` (shielded) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)
- Run 2: `formal-nonnegative` (shielded) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 2: `formal-is-even` (shielded) — CrossHair found a contract or assertion issue: is_even(1)
- Run 2: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 2: `formal-order-pair` (shielded) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 2: `formal-trim-text` (baseline) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')
- Run 2: `formal-prefix-sum` (shielded) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)
- Run 3: `formal-nonnegative` (shielded) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 3: `formal-double` (shielded) — CrossHair found a contract or assertion issue: double(-1)
- Run 3: `formal-is-even` (shielded) — CrossHair found a contract or assertion issue: is_even(1)
- Run 3: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(0, -1)
- Run 3: `formal-order-pair` (shielded) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 3: `formal-trim-text` (baseline) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')
- Run 3: `formal-prefix-sum` (shielded) — CrossHair found a contract or assertion issue: prefix_sum(1) (which returns 0)

## Raw evidence

This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.
