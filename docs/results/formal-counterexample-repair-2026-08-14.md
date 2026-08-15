# Formal counterexample-guided repair A/B (Qwen 1.5B)

> Generated: 2026-08-15 · Source schema: 2

## Reproducibility

- Commit: `10fde3c5e254efe458c86d1f3e27dc3ddcdb3513`
- Working tree dirty: `True`
- OS: `macOS-26.5.1-arm64-arm-64bit`
- Python: `3.11.9`
- Corpus: `data/formal_repair_benchmark_tasks.json`
- Corpus SHA-256: `2435e72842b4ccb24742b140e650394726f659a786eca2d85d4190f117e94608`
- Repetitions: `3`

## Configured variants

- baseline: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- shielded: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- Scope: `python-crosshair-only`

## Descriptive summary

| Measure | Baseline mean ± SD | Shielded mean ± SD | Difference |
| --- | ---: | ---: | ---: |
| Successful tasks | 3.67 ± 0.58 | 4.00 ± 0.00 | +0.33 |
| Model tokens | 949.33 ± 11.59 | 1003.67 ± 2.31 | +54.33 |
| Tool calls | 4.00 ± 0.00 | 4.00 ± 0.00 | +0.00 |
| Wall-clock seconds | 14.33 ± 2.29 | 13.41 ± 0.90 | -0.92 |

## Interpretation

The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings.

## Failures retained

- Run 1: `formal-nonnegative` (baseline) — CrossHair found a contract or assertion issue: nonnegative(-1)

## Raw evidence

This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.
