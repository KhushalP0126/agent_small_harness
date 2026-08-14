# Formal counterexample-guided repair A/B (Qwen 1.5B)

> Generated: 2026-08-14 · Source schema: 2

## Reproducibility

- Commit: `7c8b653d479f08b33fb6802a5b8a670bb729bfd9`
- Working tree dirty: `True`
- OS: `macOS-26.5.1-arm64-arm-64bit`
- Python: `3.11.9`
- Corpus: `data/formal_repair_benchmark_tasks.json`
- Corpus SHA-256: `a69409d1c882892b3f97b2d4378f8c05bd964041f84bcc68c516de7423dd7311`
- Repetitions: `3`

## Configured variants

- baseline: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- shielded: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- Scope: `python-crosshair-only`

## Descriptive summary

| Measure | Baseline mean ± SD | Shielded mean ± SD | Difference |
| --- | ---: | ---: | ---: |
| Successful tasks | 1.00 ± 1.00 | 1.67 ± 0.58 | +0.67 |
| Model tokens | 719.00 ± 41.58 | 782.33 ± 35.81 | +63.33 |
| Tool calls | 2.00 ± 0.00 | 2.00 ± 0.00 | +0.00 |
| Wall-clock seconds | 6.59 ± 2.31 | 8.09 ± 1.84 | +1.51 |

## Interpretation

The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings.

## Failures retained

- Run 1: `formal-clamp-value` (baseline) — unspecified failure
- Run 1: `formal-clamp-value` (shielded) — unspecified failure
- Run 2: `formal-clamp-value` (baseline) — unspecified failure
- Run 2: `formal-identity` (baseline) — unspecified failure

## Raw evidence

This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.
