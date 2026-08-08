# Gemma 1B and DeepSeek architect results — 2026-07-19

> Historical run record. Audited 2026-07-30 without rerunning external models.
> Provider model availability may change independently of this evaluation.

## Purpose

This evaluation replaces a proposed “weak DeepSeek worker” test with a genuinely
smaller local worker: Ollama `gemma3:1b`. It uses the same seven tasks, static
gates, behavioral cases, retry budgets, artifact capture, and run-record format
as the earlier `qwen2.5-coder:1.5b` evaluation.

The API architect remains DeepSeek V4 Pro. DeepSeek's official model list now
uses `deepseek-v4-flash` and `deepseek-v4-pro`; the legacy `deepseek-chat` and
`deepseek-reasoner` aliases retire on 2026-07-24 at 15:59 UTC. Runtime defaults,
YAML profiles, and documentation were updated to `deepseek-v4-pro` while keeping
environment overrides available.

Official references:

- [DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [DeepSeek V4 change log](https://api-docs.deepseek.com/updates/)
- [DeepSeek model-list API](https://api-docs.deepseek.com/api/list-models)

## Commands

```text
make test-coding-capability MODEL=gemma3:1b
make test-coding-capability-architect MODEL=gemma3:1b
```

Both commands saved per-attempt artifacts under `artifacts/runs` and appended
their raw records to `data/runs.jsonl`.

## Overall comparison

| Worker | Worker only | With architect | Tasks recovered by escalation |
| --- | ---: | ---: | ---: |
| `qwen2.5-coder:1.5b` | 3/7 | 6/7 | 3 |
| `gemma3:1b` | 2/7 | 6/7 | 4 |

Gemma is the weaker worker in this sample, but architect escalation raises both
workers to the same 6/7 completion level. This is direct evidence that the
repair/escalation loop can rescue a smaller model across several unrelated task
types.

## Gemma task results and retries

| Task | Gemma only | Retries | With architect | Retries | Outcome |
| --- | --- | ---: | --- | ---: | --- |
| `matrix_scoring` | manual review | 0 | completed | 1 | architect recovered |
| `dedupe_preserve_order` | completed | 0 | completed | 0 | Gemma solved initially |
| `clamp_values` | completed | 0 | completed | 0 | Gemma solved initially |
| `merge_intervals` | manual review | 1 | completed | 2 | Gemma helped; architect completed |
| `parse_key_value_lines` | manual review | 0 | manual review | 2 | branching loop detected |
| `group_top_scores` | manual review | 0 | completed | 1 | architect recovered |
| `summarize_transactions` | manual review | 0 | completed | 1 | architect recovered |

The worker-only run completed two tasks and used one repair attempt in total.
The architect run completed six tasks and used seven repairs across the suite.

## Remaining failure

`parse_key_value_lines` remained unresolved after two repairs. The final draft
still accepted `x=1=2`, returning it as `{"x": "1=2"}` instead of ignoring a
line without exactly one equals sign. The controller detected a branching loop
and routed the result to manual review rather than spending more calls on a
repeating repair path.

## Prompt-routing audit

None of the seven Gemma architect-run artifacts contained section-parser
state-machine instructions. This is correct for this suite: even the key/value
line parser is not a sectioned state machine. The generic repair prompts carried
the task examples and exact validation failures without `active_section`,
section-header, or nested-dictionary contamination.

## Interpretation

The 1B worker predictably makes more elementary mistakes than Qwen 1.5B,
including arithmetic classification, malformed-input handling, ranking, and
aggregation errors. The architect repaired four of five Gemma failures, which
is stronger evidence for the architecture than testing another large API model
in the worker role. These remain single stochastic samples, so the comparison
should be repeated before treating the one-task baseline difference as a stable
model ranking.
