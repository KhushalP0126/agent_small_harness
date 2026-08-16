# Formal counterexample-guided repair: diverse-shape corpus follow-up

> Generated: 2026-08-16 · Source schema: 2

## Reproducibility

- Commit: `aab6054aa2c1d47330d73ef086933d100e4de965`
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
| Successful tasks | 10.00 ± 0.00 | 9.33 ± 0.58 | -0.67 |
| Model tokens | 2630.67 ± 10.26 | 2817.00 ± 7.81 | +186.33 |
| Tool calls | 11.00 ± 0.00 | 11.00 ± 0.00 | +0.00 |
| Wall-clock seconds | 49.16 ± 7.39 | 44.98 ± 4.32 | -4.18 |

## Interpretation

The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings.

## Failures retained

- Run 1: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 1: `formal-order-pair` (shielded) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 1: `formal-trim-text` (shielded) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')
- Run 2: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 2: `formal-order-pair` (shielded) — CrossHair found a contract or assertion issue: ordered_pair(1, 0) (which returns (1, 0))
- Run 2: `formal-trim-text` (shielded) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')
- Run 3: `formal-order-pair` (baseline) — CrossHair found a contract or assertion issue: ordered_pair(1, 0)
- Run 3: `formal-trim-text` (shielded) — CrossHair found a contract or assertion issue: trim_text('\x00\t') (which returns '\x00\t')

## Raw evidence

This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.
