# Independent fixture benchmark — Qwen 1.5B — 2026-08-16

> Generated: 2026-08-17 · Source schema: 2

## Reproducibility

- Commit: `8fac62612a359c1007d639cc14c87437e686858f`
- Working tree dirty: `True`
- OS: `macOS-26.5.1-arm64-arm-64bit`
- Python: `3.11.9`
- Corpus: `research_fixture_tasks.json`
- Corpus SHA-256: `d6ec744654bec6fa1934185ec845e2485385c1d257ff58b728e0e9146ecce758`
- Repetitions: `3`

## Configured variants

- baseline: `ollama` / `qwen2.5-coder:1.5b` · context `8192` · thinking `not_supported` · reasoning `not_supported`
- shielded: `ollama` / `qwen2.5-coder:1.5b` · context `16384` · thinking `not_supported` · reasoning `not_supported`

## Descriptive summary

| Measure | Baseline mean ± SD | Shielded mean ± SD | Difference |
| --- | ---: | ---: | ---: |
| Successful tasks | 9.67 ± 0.58 | 8.00 ± 0.00 | -1.67 |
| Model tokens | 3744.67 ± 341.79 | 22236.67 ± 2903.49 | +18492.00 |
| Tool calls | 0.00 ± 0.00 | 23.00 ± 3.46 | +23.00 |
| Wall-clock seconds | 210.59 ± 24.04 | 344.80 ± 46.45 | +134.21 |

## Interpretation

The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings.

## Failures retained

- Run 1: `fixture-add-subtract` (shielded) — turn_limit
- Run 1: `fixture-reserve-edge-case` (shielded) — turn_limit
- Run 2: `fixture-add-subtract` (shielded) — turn_limit
- Run 2: `fixture-reserve-edge-case` (shielded) — turn_limit
- Run 2: `fixture-plan-reservation-log` (baseline) — RuntimeError: Ollama generate failed with HTTP 500: {"error":"timed out waiting for llama-server to start - "}
- Run 3: `fixture-add-subtract` (shielded) — RuntimeError: Ollama generate failed with HTTP 500: {"error":"timed out waiting for llama-server to start - "}
- Run 3: `fixture-reserve-edge-case` (shielded) — turn_limit

## Raw evidence

This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.
