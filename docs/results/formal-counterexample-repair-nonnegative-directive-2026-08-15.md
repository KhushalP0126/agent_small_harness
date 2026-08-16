# Formal counterexample-guided repair: full-corpus narrow-directive batch A

> Generated: 2026-08-16 · Source schema: 2

## Reproducibility

- Commit: `c1b6466bdc7e5040f7312b57483071e01e798241`
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
| Successful tasks | 8.00 ± 0.00 | 8.00 ± 0.00 | +0.00 |
| Model tokens | 1862.67 ± 8.08 | 1989.00 ± 0.00 | +126.33 |
| Tool calls | 8.00 ± 0.00 | 8.00 ± 0.00 | +0.00 |
| Wall-clock seconds | 33.30 ± 4.13 | 31.96 ± 0.92 | -1.33 |

## Interpretation

The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings.

## Failures retained

- No failed task outcomes were recorded.

## Raw evidence

This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.
