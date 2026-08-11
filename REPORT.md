# Research report

> Updated: 2026-08-11 · Evidence source: committed reports under
> [`docs/results/`](docs/results/)

## Executive summary

The harness has a working, approval-gated coding loop with deterministic
validation. The published experiments show that loop hardening improved task
completion, but they do **not** show a token-saving advantage. This is a useful
research result: stronger control loops can trade cost for reliability.

## Published benchmark snapshot

```mermaid
xychart-beta
    title "DeepSeek 20-task hardened run"
    x-axis ["Baseline", "Shielded"]
    y-axis "Tasks completed" 0 --> 20
    bar [20, 19]
```

```mermaid
xychart-beta
    title "DeepSeek model-token use"
    x-axis ["Baseline", "Shielded"]
    y-axis "Model tokens" 0 --> 200000
    bar [53467, 182661]
```

| Measure | Comparable baseline | Hardened shielded loop | Difference |
| --- | ---: | ---: | --- |
| Successful tasks | 20/20 | 19/20 | -1 task |
| Model tokens | 53,467 | 182,661 | 3.42× baseline |
| Tool calls | 0 | 76 | Tool-loop overhead is visible |
| Wall-clock time | 197.7s | 240.5s | 1.22× baseline |

The hardened loop improved from the earlier 11/20 shielded result to 19/20,
but that comparison is historical—not a repeated confidence estimate.

## Frozen local-model result

```mermaid
xychart-beta
    title "Qwen 1.5B Compute Shield: 10 tasks"
    x-axis ["Baseline", "Shielded"]
    y-axis "Model tokens" 0 --> 18000
    bar [5606, 16973]
```

```mermaid
pie title "Qwen 1.5B task outcomes"
    "Direct baseline success" : 10
    "Shielded success" : 9
    "Shielded failure" : 1
```

| Measure | Direct baseline | Shielded loop | Difference |
| --- | ---: | ---: | --- |
| Successful tasks | 10/10 | 9/10 | -1 task |
| Model tokens | 5,606 | 16,973 | 3.03× baseline |
| Aggregate wall time | 262.4s | 153.4s | Shielded was faster |

## What the evidence supports

```mermaid
flowchart TD
    A[Typed tools and validation] --> B[More controlled code changes]
    B --> C[Approval-gated writes]
    C --> D[Inspectable artifacts]
    D --> E[Useful prototype and research platform]
    F[Single-run benchmark data] --> G[No broad efficiency claim]
    G --> H[Repeat before generalizing]
```

Supported today:

- Reviewable multi-file diffs and explicit human approval.
- Deterministic checks, container-backed execution, and captured evidence.
- Repeatable fixed-task benchmark tooling that retains failures and raw runs.

Not supported today:

- A claim that the shielded loop saves tokens or money.
- Generalization from the present corpus to other models or repositories.
- Safe multi-user hosting.

## Next measurement

Run the exact three-repeat corpus on a terminal that can remain active for the
full experiment:

```bash
make research-agent-benchmark \
  RESEARCH_RUNS=3 \
  BASELINE_CMD="python3 scripts/run_deepseek_benchmark_agent.py --mode baseline" \
  SHIELDED_CMD="python3 scripts/run_deepseek_benchmark_agent.py --mode shielded" \
  RESEARCH_OUTPUT=artifacts/research/deepseek-repeated.json
```

Publish the raw JSON and a dated report whether the results improve or not.
That is the last step needed to move from a compelling prototype report to a
repeatable research claim.

The report renderer and second corpus are now part of the reproducibility
surface. After a raw run completes, render its committed report with:

```bash
make research-report \
  REPORT_INPUT=docs/results/raw/deepseek-repeated.json \
  REPORT_OUTPUT=docs/results/deepseek-repeated-YYYY-MM-DD.md
```

Then run `make research-fixture-deepseek` and `make research-fixture-qwen` on
the versioned fixture corpus. The evidence remains incomplete until those raw
reports and the five approval-session receipts are published.
