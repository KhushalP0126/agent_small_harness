# Targeted nonnegative postcondition directive: 6-repeat paired diagnosis

> Generated: 2026-08-16 · Source schema: 2

## Reproducibility

- Commit: `2f83fbd30067df242907b802ace206f7eb54a047`
- Working tree dirty: `True`
- OS: `macOS-26.5.1-arm64-arm-64bit`
- Python: `3.11.9`
- Corpus: `data/formal_nonnegative_benchmark_tasks.json`
- Corpus SHA-256: `f9ddba21171dc6a1a15b9aaf921fbe68a9a4b6fe4a12f75d09d852c53ccc4e2b`
- Repetitions: `6`

## Configured variants

- baseline: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- shielded: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_applicable` · reasoning `not_applicable`
- Scope: `python-crosshair-only`

## Descriptive summary

| Measure | Baseline mean ± SD | Shielded mean ± SD | Difference |
| --- | ---: | ---: | ---: |
| Successful tasks | 1.00 ± 0.00 | 1.00 ± 0.00 | +0.00 |
| Model tokens | 239.00 ± 0.00 | 255.00 ± 0.00 | +16.00 |
| Tool calls | 1.00 ± 0.00 | 1.00 ± 0.00 | +0.00 |
| Wall-clock seconds | 4.24 ± 2.44 | 3.37 ± 0.58 | -0.87 |

## Interpretation

The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings.

## Failures retained

- No failed task outcomes were recorded.

## Raw evidence

This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.
