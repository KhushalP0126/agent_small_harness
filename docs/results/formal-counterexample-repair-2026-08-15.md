# Formal counterexample-guided repair: 8-task paired study

> Generated: 2026-08-15 · Source schema: 2

## Reproducibility

- Commit: `3ab99a38ce8addc39e285476b528c090f816459d`
- Working tree dirty: `True`
- OS: `macOS-26.5.1-arm64-arm-64bit`
- Python: `3.11.9`
- Corpus: `data/formal_repair_benchmark_tasks.json`
- Corpus SHA-256: `3f928d079b01b38af09dd9c720918dca440fdf4f1079db063a31e216a7cc053c`
- Repetitions: `3`

## Configured variants

- baseline: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- shielded: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- Scope: `python-crosshair-only`

## Descriptive summary

| Measure | Baseline mean ± SD | Shielded mean ± SD | Difference |
| --- | ---: | ---: | ---: |
| Successful tasks | 8.00 ± 0.00 | 7.67 ± 0.58 | -0.33 |
| Model tokens | 1853.33 ± 8.08 | 1978.00 ± 5.20 | +124.67 |
| Tool calls | 8.00 ± 0.00 | 8.00 ± 0.00 | +0.00 |
| Wall-clock seconds | 30.76 ± 3.87 | 29.63 ± 2.09 | -1.13 |

## Interpretation

The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings.

## Failures retained

- Run 2: `formal-nonnegative` (shielded) — CrossHair found a contract or assertion issue: nonnegative(-1)

## Raw evidence

This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.
