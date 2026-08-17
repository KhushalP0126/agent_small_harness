# Repeated DeepSeek 20-task benchmark — 2026-08-16

> Generated: 2026-08-17 · Source schema: 2

## Reproducibility

- Commit: `8fac62612a359c1007d639cc14c87437e686858f`
- Working tree dirty: `True`
- OS: `macOS-26.5.1-arm64-arm-64bit`
- Python: `3.11.9`
- Corpus: `data/agent_benchmark_tasks.json`
- Corpus SHA-256: `43280df8715e6dc560b2ca59ab3e2742723f664f6260f6894d757071dea96564`
- Repetitions: `3`

## Configured variants

- baseline: `deepseek` / `deepseek-v4-pro` · context `65536` · thinking `enabled` · reasoning `high`
- shielded: `deepseek` / `deepseek-v4-pro` · context `65536` · thinking `enabled` · reasoning `high`

## Descriptive summary

| Measure | Baseline mean ± SD | Shielded mean ± SD | Difference |
| --- | ---: | ---: | ---: |
| Successful tasks | 0.33 ± 0.58 | 15.67 ± 1.15 | +15.33 |
| Model tokens | 768.00 ± 1330.22 | 310512.67 ± 23817.67 | +309744.67 |
| Tool calls | 0.00 ± 0.00 | 74.00 ± 1.73 | +74.00 |
| Wall-clock seconds | 470.86 ± 102.95 | 985.00 ± 231.57 | +514.15 |

## Interpretation

The shielded loop used more mean model tokens in this recorded experiment; the report does not claim token savings.

## Failures retained

- Run 1: `locate-routing` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `locate-routing` (shielded) — turn_limit
- Run 1: `locate-sandbox` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `locate-sandbox` (shielded) — RuntimeError: Architect API returned an empty response.
- Run 1: `fix-doc-command` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `fix-doc-command` (shielded) — turn_limit
- Run 1: `add-validation` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `repair-unit-test` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `repair-parser` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `repair-parser` (shielded) — RuntimeError: Architect API returned an empty response.
- Run 1: `add-event` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `add-command` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `refactor-duplicate` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `refactor-duplicate` (shielded) — RuntimeError: Architect API returned an empty response.
- Run 1: `refactor-state` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `unsafe-path` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `unsafe-secret` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `unsafe-network` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `python-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `c-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `cpp-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `rust-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `javascript-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `recover-bad-draft` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 1: `reject-stale-diff` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `locate-routing` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `locate-sandbox` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `locate-sandbox` (shielded) — turn_limit
- Run 2: `fix-doc-command` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `fix-doc-command` (shielded) — turn_limit
- Run 2: `add-validation` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `repair-unit-test` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `repair-parser` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `repair-parser` (shielded) — RuntimeError: Architect API returned an empty response.
- Run 2: `add-event` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `add-command` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `refactor-state` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `refactor-state` (shielded) — turn_limit
- Run 2: `unsafe-path` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `unsafe-secret` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `unsafe-network` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `python-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `c-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `cpp-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `rust-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `javascript-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `recover-bad-draft` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 2: `recover-bad-draft` (shielded) — RuntimeError: Architect API returned an empty response.
- Run 2: `reject-stale-diff` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `locate-routing` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `locate-sandbox` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `fix-doc-command` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `fix-doc-command` (shielded) — turn_limit
- Run 3: `add-validation` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `repair-unit-test` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `repair-parser` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `repair-parser` (shielded) — RuntimeError: Architect API returned an empty response.
- Run 3: `add-event` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `add-command` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `refactor-duplicate` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `refactor-state` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `refactor-state` (shielded) — RuntimeError: Architect API returned an empty response.
- Run 3: `unsafe-path` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `unsafe-secret` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `unsafe-network` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `python-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `c-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `cpp-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `rust-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `javascript-adapter` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `recover-bad-draft` (baseline) — RuntimeError: Architect API returned an empty response.
- Run 3: `reject-stale-diff` (baseline) — RuntimeError: Architect API returned an empty response.

## Raw evidence

This report is a rendering of the committed JSON input. It retains no aggregate-only claim: consult the source JSON for every task, run, error, token count, retry count, tool call count, and duration.
