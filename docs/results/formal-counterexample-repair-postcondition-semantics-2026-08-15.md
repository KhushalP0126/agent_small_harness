# Formal counterexample-guided repair: postcondition-semantics follow-up

> Generated: 2026-08-16 · Source schema: 2

## Reproducibility

- Commit: `e0746f6df96980c334ab5d87f717bc10cbfde1c6`
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
| Successful tasks | 8.00 ± 0.00 | 7.33 ± 0.58 | -0.67 |
| Model tokens | 2060.00 ± 0.00 | 2197.00 ± 5.20 | +137.00 |
| Tool calls | 8.00 ± 0.00 | 8.00 ± 0.00 | +0.00 |
| Wall-clock seconds | 37.95 ± 7.73 | 36.87 ± 4.72 | -1.08 |

## Interpretation

The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings.

## Failures retained

- Run 1: `formal-nonnegative` (shielded) — CrossHair found a contract or assertion issue: nonnegative(-1)
- Run 2: `formal-nonnegative` (shielded) — CrossHair found a contract or assertion issue: nonnegative(-1)

## Raw evidence

This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.
